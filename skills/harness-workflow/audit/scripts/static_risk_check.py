#!/usr/bin/env python3
r"""static_risk_check.py —— R-IMP-004 实装（合入必需检查）+ R-IMP-003 空断言部分。

审什么：diff **新增行**中的测试完整性反模式（内置模式 + 项目追加规则）：
  1. 跳过标记：pytest.mark.skip/skipif/xfail、pytest.skip()、t.Skip、it/describe.skip、
     xit/xdescribe/xcontext、@Disabled、@Ignore（java/kotlin）、[Ignore（C#）、
     #[ignore（rust）、unittest.skip 等（新增跳过标记一律违规——删除标记不算）；
  2. 吞异常的空捕获：except ...: pass / ...（单行 + 相邻两行形态——black/ruff
   格式化会把单行拆成两行，v12 起两行形态可检出）、contextlib.suppress(...)、
     catch (...) {} 等。except 头与空体之间可夹至多一行注释（新增或上下文注释，
     v13 起统一为检出，方向保守）；
  3. 空断言凑数：assert True / assertTrue(true) / expect(true).toBe(true) 等
     （对应 R-IMP-003"禁止以空断言凑数"；该条的测试数量/覆盖率部分需 CI，见 ci_gate_check）。

判据配置（GOVERNANCE_DIR/redlines.yaml，可选；存在但非法 → 42）：
  - static_risk_allowlist：正则字符串列表，命中的行视为项目显式豁免（应经人审，E-06）；
  - static_risk_extra_rules：正则字符串列表，**项目追加的自有反模式规则**（自研
    框架的自定义跳过 DSL 从这里补），命中即违规——这是新增规则的唯一入口。

散文豁免：*.md / *.txt / *.rst 中的新增行不扫内容模式（与 risk_floor_check 的
skip_paths 黑名单同口径，避免文档示例代码误报）；文件仍计入 diff 覆盖核对。

diff 解析（fail-closed，v10 加固）：
  - 文件头只在 hunk 外识别："diff --git" 开启文件节；"--- " 置头期待；"+++ "
    消费头期待并归因。hunk 内以 "+" 开头的行（含内容本身以 "++" 开头的新增行，
    如 "+++ b/other.js" 或 "+++it.skip(...)"）一律按新增内容扫描，**不再被误当
    文件头**——堵住"新增一行伪文件头把后续违规行归到别处"的绕过；
  - 兼容 git 配置差异：core.quotepath（引号 + 八进制转义路径）、core.autocrlf 无关、
    diff.noprefix（无 a//b/ 前缀）、diff.mnemonicPrefix（w/、i/ 前缀）、/dev/null；
  - changes.json 列出的文件若在 diff 中**完全无归属**（无任何文件头）→ 退出 42
    （证据不完整，fail-closed；不得对未覆盖的变更报 PASS）。

已知盲区（报告须注明"机器推算仅供参考，需人审"）：
  - 多行空 catch：仅识别"except 头行 + 相邻空体行（pass/...），或头体间夹至多
    一行注释"；头与体之间夹**两行及以上**注释/代码、或空体为真正业务代码不识别；
  - 动态构造的跳过（如 mark.skipif 条件为真值变量）、RSpec skip/pending、
    go `if err != nil {}` 空分支、`assert x or True` 类恒真表达不识别——
    自研框架的自定义跳过 DSL 请用 static_risk_extra_rules 补。

退出码：0 通过 / 1 发现违规（阻断级）/ 42 未接线（判据文件损坏、diff 未覆盖
changes 列出的文件等 fail-closed）。

用法：
  static_risk_check.py --changes <changes.json> --diff <diff.txt>
      [--governance-dir <dir>] [--self-test]
  changes.json: [{"path": "...", "status": "M"}]   diff: unified diff 文本
"""
import os
import sys
import io
import contextlib
import json
import re
import difflib
import tempfile
import shutil

try:
    import yaml
except ImportError:
    yaml = None

VIOLATION = 1
OK = 0
NOT_WIRED = 42
GOVERNANCE_DIR = os.environ.get("GOVERNANCE_DIR", "governance")
PROSE_EXT = (".md", ".txt", ".rst")

# ---- 内置模式（R-IMP-004 / R-IMP-003 空断言部分）----
SKIP_MARKS = [
    (r"@pytest\.mark\.(skip|skipif|xfail)\b", "pytest skip/skipif/xfail mark"),
    (r"\bpytest\.(skip|fail|xfail)\s*\(", "pytest runtime skip"),
    (r"\bt\.Skip\s*\(", "go t.Skip"),
    (r"\b(it|test|describe)\.skip\b", "js/ts skip"),
    (r"\b(xit|xdescribe|xcontext)\b", "js/rspec x-prefixed skip"),
    (r"@(Disabled|Ignore)\b", "java/kotlin @Disabled/@Ignore"),
    (r"#\[ignore\b", "rust #[ignore]"),
    (r"\[\s*Ignore\s*(\(|\])", "c# [Ignore] attribute"),
    (r"\btdd_skip\b|\bunittest\.skip\b|@unittest\.skip", "python unittest skip"),
]
EMPTY_CATCH = [
    # 单行形态：裸 except:、except E:、except (A, B):、except E as e:，
    # 空体为 pass 或 Ellipsis（...）
    (r"\bexcept\b[^:#\n]*:\s*(?:pass\b|\.\.\.)", "python empty except (same line)"),
    (r"catch\s*\([^)]*\)\s*\{\s*\}", "js/java/kotlin/c# empty catch block"),
    (r"\bcontextlib\.suppress\s*\(", "python contextlib.suppress (exception swallowed)"),
    (r"\bwith\s+suppress\s*\(", "python suppress() (exception swallowed)"),
]
# v12：相邻两行空 catch（black/ruff 格式化会把 `except X: pass` 拆成两行）。
# EXCEPT_HEADER 匹配"以冒号收尾的 except 头"（可带尾注释）；BARE_NOOP_BODY 匹配
# 空体行（pass / ...，可带尾注释）。两行在新增行序列中相邻即报（夹注释行的
# 形态仍属盲区，见 docstring）。
EXCEPT_HEADER = re.compile(r"^\s*except\b[^:#]*:\s*(#.*)?$")
BARE_NOOP_BODY = re.compile(r"^\s*(?:pass|\.\.\.)\s*(#.*)?$")
EMPTY_ASSERT = [
    # 恒真断言（凑数）：assert True / assert 1 须到行尾（否则 assert 1 == 1 是合法断言）；
    # assert 0 / assert False 是 fail-fast 惯用法，不算凑数
    (r"\bassert\s+True\s*(#.*)?$", "python assert True (always true)"),
    (r"\bassert\s+1\s*(#.*)?$", "python assert 1 (always true)"),
    (r"\bassertTrue\s*\(\s*true\s*\)", "java assertTrue(true)"),
    (r"\bexpect\s*\(\s*true\s*\)\s*\.\s*toBe\s*\(\s*true\s*\)", "js expect(true).toBe(true)"),
]
ALL_PATTERNS = [("skip", p, why) for p, why in SKIP_MARKS] + \
               [("empty-catch", p, why) for p, why in EMPTY_CATCH] + \
               [("empty-assert", p, why) for p, why in EMPTY_ASSERT]
COMPILED = [(kind, re.compile(p, re.IGNORECASE), why) for kind, p, why in ALL_PATTERNS]

_last = []  # 供 harness 复用：最近一次运行的发现


def _unquote_git_path(p):
    """git 对含非 ASCII/空格/特殊字符的路径加引号并做 C 风格转义（八进制字节）。"""
    if not (len(p) >= 2 and p.startswith('"') and p.endswith('"')):
        return p
    body = p[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            n = body[i + 1]
            simple = {"n": b"\n", "t": b"\t", "r": b"\r", '"': b'"', "\\": b"\\"}
            if n in simple:
                out += simple[n]
                i += 2
                continue
            m = re.match(r"[0-7]{1,3}", body[i + 1:])
            if m:
                out.append(int(m.group(0), 8))
                i += 1 + len(m.group(0))
                continue
        out += c.encode("utf-8")
        i += 1
    return out.decode("utf-8", "replace")


def _strip_diff_prefix(p):
    """去掉 a/ b/ w/ i/ o/ 等前缀（noprefix 配置下没有前缀，原样返回）。"""
    for pre in ("b/", "a/", "w/", "i/", "o/"):
        if p.startswith(pre) and len(p) > len(pre):
            return p[len(pre):]
    return p


def _norm_git_path(raw):
    p = _strip_diff_prefix(_unquote_git_path(raw.strip()))
    return None if p == "/dev/null" else p


def _diff_added(diff_text):
    """按文件头归因 diff 新增行；返回 (by_file, seen_paths, seq_by_file)。

    by_file: {path: [added_line, ...]}（仅新增行，供单行正则扫描）
    seq_by_file: {path: [(is_added, text), ...]}（hunk 内新增/上下文行的有序序列，
      供多行空 catch 检测——上下文行 is_added=False，可跨注释行配对）
    头判定只在 hunk 外进行（防内容行劫持）："diff --git" 开启文件节；"--- " 置
    头期待；"+++ " 在 hunk 外一律按文件头处理；hunk 内 "+" 开头的行全部是新增
    内容——即使内容以 "++" 开头也不再被误当文件头。
    """
    by_file, seen, seq_by_file = {}, set(), {}
    cur = None
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            cur = None
            # 尽力从节头记 seen（二进制文件/纯删除文件可能没有 +++ 行）
            m = re.match(r'^diff --git (?P<a>.+?) (?P<b>.+)$', line)
            if m:
                p = _norm_git_path(m.group("b"))
                if p:
                    seen.add(p)
            continue
        if not in_hunk and line.startswith("--- "):
            continue  # 旧路径头，仅作分隔；归因以 +++ 为准
        if not in_hunk and line.startswith("+++ "):
            p = _norm_git_path(line[4:])
            if p is not None:
                cur = p
                seen.add(p)
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if line.startswith(("Binary files ", "GIT binary patch")):
            in_hunk = False
            m = re.match(r"Binary files .* and (.+) differ", line)
            if m:
                p = _norm_git_path(m.group(1))
                if p:
                    seen.add(p)
            continue
        if cur and line.startswith("+"):
            body = line[1:]
            by_file.setdefault(cur, []).append(body)
            seq_by_file.setdefault(cur, []).append((True, body))
        elif cur and (line.startswith(" ") or line.startswith("\t")):
            # 上下文行：记入序列供多行空 catch 配对，但不扫内容模式
            seq_by_file.setdefault(cur, []).append((False, line[1:]))
    return by_file, seen, seq_by_file


def _is_comment(text):
    """新增/上下文行是否为注释行（python/js/ts/rust/c#/go 常见注释前缀）。"""
    s = text.strip()
    return s.startswith("#") or s.startswith("//")


def _load_rules(path, key):
    """读 redlines.yaml 中 key 指定的正则字符串列表（可选；损坏 → 42）。"""
    if not os.path.exists(path):
        return {}, []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        print("static_risk_check: redlines.yaml is corrupt (not valid YAML); treat as "
              "NOT-WIRED and fix the file. Path: %s\n  %s" % (path, e), file=sys.stderr)
        sys.exit(42)
    if not isinstance(data, dict):
        print("static_risk_check: redlines.yaml is not a YAML mapping; treat as "
              "NOT-WIRED. Path: %s" % path, file=sys.stderr)
        sys.exit(42)
    raw = data.get(key)
    if raw is None:
        return {}, []
    if not isinstance(raw, list):
        print("static_risk_check: %r must be a list of regex strings (got %s); "
              "treat as NOT-WIRED." % (key, type(raw).__name__), file=sys.stderr)
        sys.exit(42)
    out = []
    for a in raw:
        if not isinstance(a, str) or not a.strip():
            print("static_risk_check: %s entries must be non-empty strings "
                  "(got %r); treat as NOT-WIRED." % (key, a), file=sys.stderr)
            sys.exit(42)
        try:
            out.append(re.compile(a))
        except re.error as e:
            print("static_risk_check: invalid regex in %s %r (%s); treat as "
                  "NOT-WIRED." % (key, a, e), file=sys.stderr)
            sys.exit(42)
        if out[-1].search(""):
            # v12：能匹配空串的正则必然命中每一行（search 语义）——豁免 = 全豁免、
            # 追加规则 = 全违规。不改变退出码，但必须让使用者在输出里看见。
            print("static_risk_check: WARNING pattern %r in %s matches the empty "
                  "string and therefore matches EVERY added line; confirm this is "
                  "intended (human review per E-06)." % (a, key), file=sys.stderr)
    return data, out


def load_config():
    """读 redlines.yaml：static_risk_allowlist（豁免）+ static_risk_extra_rules（追加规则）。"""
    if yaml is None:
        print("static_risk_check: pyyaml required (pip install pyyaml).", file=sys.stderr)
        sys.exit(42)
    path = os.path.join(os.environ.get("GOVERNANCE_DIR", GOVERNANCE_DIR), "redlines.yaml")
    _, allow = _load_rules(path, "static_risk_allowlist")
    _, extra = _load_rules(path, "static_risk_extra_rules")
    return allow, extra


def scan(changes, diff_text, allowlist=(), extra=()):
    """返回 (findings, uncovered)：

    findings = [(kind, why, path, line_no, line_text), ...]；allowlist 命中的行豁免。
    uncovered = changes 中列出、但 diff 里完全无归属（无任何文件头）的路径——
    调用方应据此退出 42（证据不完整，fail-closed）。
    """
    findings = []
    by_file, seen, seq_by_file = _diff_added(diff_text)
    for ch in (changes or []):
        p = ch.get("path", "") if isinstance(ch, dict) else ""
        if not p:
            continue
        if p not in seen:
            continue  # 无归属：不计发现，交由 uncovered 上报
        if p.lower().endswith(PROSE_EXT):
            continue  # 散文豁免（与 risk_floor_check 同口径），文件仍算已覆盖
        lines = by_file.get(p, [])
        for i, ln in enumerate(lines, 1):
            if any(rx.search(ln) for rx in allowlist):
                continue
            for kind, rx, why in COMPILED:
                m = rx.search(ln)
                if m:
                    findings.append((kind, why, p, i, ln.strip()))
                    break  # 一行报一条，防噪音
            else:
                for rx in extra:
                    if rx.search(ln):
                        findings.append(("project-rule", str(rx.pattern), p, i, ln.strip()))
                        break
        # v13：相邻两行空 catch（except 头行 + pass/... 空体行），且头体间可夹至多
        # 一行注释（新增或上下文，方向对齐为保守：检出）。单行正则对 black/ruff
        # 拆两行的形态失效，这里补上；注释行不计入独立单测盲区。
        seq = seq_by_file.get(p, [])
        nseq = len(seq)
        i = 0
        while i < nseq:
            a0, t0 = seq[i]
            if a0 and EXCEPT_HEADER.search(t0):
                body = None
                if i + 1 < nseq:
                    a1, t1 = seq[i + 1]
                    if _is_comment(t1):                      # 头体间夹一行注释（新增或上下文）
                        if i + 2 < nseq and seq[i + 2][0] \
                                and BARE_NOOP_BODY.search(seq[i + 2][1]):
                            body = i + 2
                    elif a1 and BARE_NOOP_BODY.search(t1):   # 紧邻空体行
                        body = i + 1
                if body is not None:
                    if any(rx.search(t0) or rx.search(seq[body][1])
                           for rx in allowlist):
                        i = body + 1
                        continue
                    findings.append(("empty-catch", "python empty except (two-line form)",
                                     p, i + 1,
                                     (t0.strip() + " / … / " + seq[body][1].strip())[:120]))
                    i = body + 1
                    continue
            i += 1
    _last.clear()
    _last.extend(findings)
    uncovered = sorted({ch.get("path") for ch in (changes or [])
                        if isinstance(ch, dict) and ch.get("path")
                        and ch["path"] not in seen})
    return findings, uncovered


def main(argv):
    if "--self-test" in argv:
        return self_test()
    changes_path = diff_path = None
    i = 0
    while i < len(argv):
        if argv[i] == "--changes":
            changes_path = argv[i + 1]; i += 2
        elif argv[i] == "--diff":
            diff_path = argv[i + 1]; i += 2
        else:
            i += 1
    if not changes_path or not diff_path:
        print("static_risk_check: --changes and --diff required (or --self-test).",
              file=sys.stderr)
        return NOT_WIRED
    with open(changes_path, encoding="utf-8") as f:
        changes = json.load(f)
    with open(diff_path, encoding="utf-8") as f:
        diff_text = f.read()
    allow, extra = load_config()
    findings, uncovered = scan(changes, diff_text, allow, extra)
    if uncovered:
        for p in uncovered:
            print("NOT-WIRED change listed in changes.json but not attributable to "
                  "any file header in the diff (evidence incomplete, fail-closed): %s"
                  % p, file=sys.stderr)
        print("static_risk_check: %d uncovered change(s) -> NOT-WIRED (42). Provide a "
              "diff covering every listed file (check diff.noprefix / quotePath / "
              "truncated diffs)." % len(uncovered), file=sys.stderr)
        return NOT_WIRED
    if findings:
        for kind, why, p, i, ln in findings:
            print("STATIC-RISK[%s] %s at %s:%d: %s" % (kind, why, p, i, ln[:120]),
                  file=sys.stderr)
        print("static_risk_check: %d finding(s) -> VIOLATION (R-IMP-004 / R-IMP-003). "
              "Remove skip marks / empty catches / empty asserts, or obtain human "
              "exemption recorded in proposals/." % len(findings), file=sys.stderr)
        return VIOLATION
    print("static_risk_check: PASS (no skip marks / empty catches / empty asserts "
          "in added lines; all listed changes attributed).")
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="src-risk-")
    old_gov = os.environ.get("GOVERNANCE_DIR")
    os.environ.pop("GOVERNANCE_DIR", None)
    try:
        def wr(name, text):
            p = os.path.join(tmp, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return p

        def chg(name, paths):
            p = os.path.join(tmp, name)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(paths, f)
            return p

        def run(c, d):
            try:
                return main(["--changes", c, "--diff", d])
            except SystemExit as e:
                return e.code

        def hdr(p):
            return "diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n" % (p, p, p, p)

        # 场景1：pytest skip mark -> 违规
        rc1 = run(chg("c1.json", [{"path": "tests/test_a.py", "status": "M"}]),
                  wr("d1.txt", hdr("tests/test_a.py") + "@@ -1,2 +1,3 @@\n"
                     "+@pytest.mark.skip(reason='later')\n"
                     "+def test_x():\n+    assert 1 == 1\n"))
        assert rc1 == VIOLATION, "expected VIOLATION (pytest skip mark), got %d" % rc1

        # 场景2：t.Skip（go）-> 违规
        rc2 = run(chg("c2.json", [{"path": "pkg/x_test.go", "status": "M"}]),
                  wr("d2.txt", hdr("pkg/x_test.go") + "+func TestX(t *testing.T) {\n"
                     "+    t.Skip(\"wip\")\n+}\n"))
        assert rc2 == VIOLATION, "expected VIOLATION (go t.Skip), got %d" % rc2

        # 场景3：空 catch -> 违规
        rc3 = run(chg("c3.json", [{"path": "src/app.js", "status": "M"}]),
                  wr("d3.txt", hdr("src/app.js") + "+try { risky(); } catch (e) {}\n"))
        assert rc3 == VIOLATION, "expected VIOLATION (empty catch), got %d" % rc3

        # 场景4：except Exception: / as e: / (A,B): pass -> 违规（v10 补）
        for py_line in ("except Exception: pass", "except ValueError as e: pass",
                        "except (A, B): pass", "except: pass"):
            d = wr("d4.txt", hdr("src/app.py") + "+" + py_line + "\n")
            rc4 = run(chg("c4.json", [{"path": "src/app.py", "status": "M"}]), d)
            assert rc4 == VIOLATION, "expected VIOLATION (%r), got %d" % (py_line, rc4)

        # 场景5：删除行中的 skip 标记不算（只扫新增行）
        rc5 = run(chg("c5.json", [{"path": "tests/test_b.py", "status": "M"}]),
                  wr("d5.txt", hdr("tests/test_b.py") + "-@pytest.mark.skip(reason='done')\n"
                     "+def test_ok():\n+    assert 1 == 1\n"))
        assert rc5 == OK, "expected PASS (deleted skip line not scanned), got %d" % rc5

        # 场景6：干净新增 -> 通过
        rc6 = run(chg("c6.json", [{"path": "src/lib.py", "status": "M"}]),
                  wr("d6.txt", hdr("src/lib.py") + "+def add(a, b):\n+    return a + b\n"))
        assert rc6 == OK, "expected PASS (clean code), got %d" % rc6

        # 场景7：上下文行中的 skip 标记不算
        rc7 = run(chg("c7.json", [{"path": "tests/test_c.py", "status": "M"}]),
                  wr("d7.txt", hdr("tests/test_c.py") + " @pytest.mark.skip(reason='ctx')\n"
                     "+def test_new():\n+    assert 2 == 2\n"))
        assert rc7 == OK, "expected PASS (context line not scanned), got %d" % rc7

        # 场景8：allowlist 豁免 -> 通过
        bd = os.path.join(tmp, "gov")
        os.makedirs(bd, exist_ok=True)
        with open(os.path.join(bd, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"static_risk_allowlist": [r"@pytest\.mark\.xfail"]}, f)
        os.environ["GOVERNANCE_DIR"] = bd
        rc8 = run(chg("c8.json", [{"path": "tests/test_d.py", "status": "M"}]),
                  wr("d8.txt", hdr("tests/test_d.py") + "+@pytest.mark.xfail(strict=True)\n"
                     "+def test_known_bug():\n+    assert 0\n"))
        assert rc8 == OK, "expected PASS (allowlisted xfail), got %d" % rc8

        # 场景9：allowlist 之外的 skip 仍违规
        rc9 = run(chg("c9.json", [{"path": "tests/test_e.py", "status": "M"}]),
                  wr("d9.txt", hdr("tests/test_e.py") + "+@pytest.mark.skip\n+def test_s():\n"
                     "+    assert True\n"))
        assert rc9 == VIOLATION, "expected VIOLATION (skip not in allowlist), got %d" % rc9
        os.environ.pop("GOVERNANCE_DIR", None)

        # 场景10：redlines.yaml 存在但损坏 -> 42
        bd2 = os.path.join(tmp, "gov2")
        os.makedirs(bd2, exist_ok=True)
        with open(os.path.join(bd2, "redlines.yaml"), "w", encoding="utf-8") as f:
            f.write("static_risk_allowlist: [broken\n  - :: {{{")
        os.environ["GOVERNANCE_DIR"] = bd2
        rc10 = run(chg("c10.json", [{"path": "src/x.py", "status": "M"}]),
                   wr("d10.txt", hdr("src/x.py") + "+x = 1\n"))
        assert rc10 == NOT_WIRED, "expected 42 (corrupt redlines), got %d" % rc10
        os.environ.pop("GOVERNANCE_DIR", None)

        # 场景11：allowlist 非列表 -> 42
        bd3 = os.path.join(tmp, "gov3")
        os.makedirs(bd3, exist_ok=True)
        with open(os.path.join(bd3, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"static_risk_allowlist": "oops"}, f)
        os.environ["GOVERNANCE_DIR"] = bd3
        rc11 = run(chg("c11.json", [{"path": "src/y.py", "status": "M"}]),
                   wr("d11.txt", hdr("src/y.py") + "+y = 2\n"))
        assert rc11 == NOT_WIRED, "expected 42 (allowlist not a list), got %d" % rc11
        os.environ.pop("GOVERNANCE_DIR", None)

        # 场景12：空断言凑数（java 形态）-> 违规
        rc12 = run(chg("c12.json", [{"path": "tests/A.java", "status": "M"}]),
                   wr("d12.txt", hdr("tests/A.java") + "+    assertTrue(true);\n"))
        assert rc12 == VIOLATION, "expected VIOLATION (assertTrue(true)), got %d" % rc12

        # 场景13：无 GOVERNANCE_DIR 也能跑（内置模式即判据，不要求配置存在）
        rc13 = run(chg("c13.json", [{"path": "src/z.py", "status": "M"}]),
                   wr("d13.txt", hdr("src/z.py") + "+z = 3\n"))
        assert rc13 == OK, "expected PASS without governance config, got %d" % rc13

        # 场景14：js describe.skip -> 违规
        rc14 = run(chg("c14.json", [{"path": "tests/app.test.ts", "status": "M"}]),
                   wr("d14.txt", hdr("tests/app.test.ts") + "+describe.skip('old', () => {});\n"))
        assert rc14 == VIOLATION, "expected VIOLATION (describe.skip), got %d" % rc14

        # ---- v10 加固场景 ----
        # 场景15：diff.noprefix（+++ 无 b/ 前缀）-> 违规（v10 前漏检）
        rc15 = run(chg("c15.json", [{"path": "a.js", "status": "M"}]),
                   wr("d15.txt", "diff --git a/a.js b/a.js\n+++ a.js\n+++it.skip('x')\n"))
        assert rc15 == VIOLATION, "expected VIOLATION (noprefix header), got %d" % rc15

        # 场景16：mnemonicPrefix（+++ w/）-> 违规（v10 前漏检）
        rc16 = run(chg("c16.json", [{"path": "a.js", "status": "M"}]),
                   wr("d16.txt", "diff --git a/a.js b/a.js\n+++ w/a.js\n+++it.skip('x')\n"))
        assert rc16 == VIOLATION, "expected VIOLATION (mnemonic header), got %d" % rc16

        # 场景17：quotepath 八进制转义路径 -> 违规（v10 前漏检）
        q = 'diff --git "a/\\346\\265\\213.js" "b/\\346\\265\\213.js"\n' \
            '+++ "b/\\346\\265\\213.js"\n+it.skip("x")\n'
        rc17 = run(chg("c17.json", [{"path": "测.js", "status": "M"}]),
                   wr("d17.txt", q))
        assert rc17 == VIOLATION, "expected VIOLATION (quoted octal path), got %d" % rc17

        # 场景18：内容以 ++ 开头的新增行不再是文件头（v10 前被丢弃=漏检）
        rc18 = run(chg("c18.json", [{"path": "a.js", "status": "M"}]),
                   wr("d18.txt", hdr("a.js") + "@@ -1,2 +1,3 @@\n"
                      "+++it.skip('inline-plus-plus')\n"))
        assert rc18 == VIOLATION, "expected VIOLATION (++ content line), got %d" % rc18

        # 场景19：伪文件头劫持被堵：hunk 内新增 "+++ b/other.js" 后，真违规仍归原文件
        rc19 = run(chg("c19.json", [{"path": "a.js", "status": "M"}]),
                   wr("d19.txt", hdr("a.js") + "@@ -1,2 +1,3 @@\n+console.log(1)\n"
                      "+++ b/other.js\n+it.skip('hijacked')\n"))
        assert rc19 == VIOLATION, "expected VIOLATION (hijack blocked), got %d" % rc19

        # 场景20：changes 列出的文件在 diff 中无归属 -> 42（fail-closed）
        rc20 = run(chg("c20.json", [{"path": "a.js", "status": "M"}]),
                   wr("d20.txt", hdr("z.js") + "+clean()\n"))
        assert rc20 == NOT_WIRED, "expected 42 (uncovered change), got %d" % rc20

        # 场景21：rust #[ignore] -> 违规（v10 补）
        rc21 = run(chg("c21.json", [{"path": "x.rs", "status": "M"}]),
                   wr("d21.txt", hdr("x.rs") + "+#[ignore]\n+fn t() {}\n"))
        assert rc21 == VIOLATION, "expected VIOLATION (rust ignore), got %d" % rc21

        # 场景22：c# [Ignore("...")] -> 违规（v10 补）
        rc22 = run(chg("c22.json", [{"path": "T.cs", "status": "M"}]),
                   wr("d22.txt", hdr("T.cs") + "+[Ignore(\"why\")]\n+public void T() {}\n"))
        assert rc22 == VIOLATION, "expected VIOLATION (csharp Ignore), got %d" % rc22

        # 场景23：skipif -> 违规（v10 补）
        rc23 = run(chg("c23.json", [{"path": "t.py", "status": "M"}]),
                   wr("d23.txt", hdr("t.py") + "+@pytest.mark.skipif(sys.platform == 'win')\n"
                      "+def test_x(): pass\n"))
        assert rc23 == VIOLATION, "expected VIOLATION (skipif), got %d" % rc23

        # 场景24：static_risk_extra_rules 项目追加规则 -> 违规
        bd4 = os.path.join(tmp, "gov4")
        os.makedirs(bd4, exist_ok=True)
        with open(os.path.join(bd4, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"static_risk_extra_rules": [r"myCustomSkip\("]}, f)
        os.environ["GOVERNANCE_DIR"] = bd4
        rc24 = run(chg("c24.json", [{"path": "t.js", "status": "M"}]),
                   wr("d24.txt", hdr("t.js") + "+myCustomSkip('wip');\n"))
        assert rc24 == VIOLATION, "expected VIOLATION (extra rule), got %d" % rc24
        # 非法 extra 规则 -> 42
        with open(os.path.join(bd4, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"static_risk_extra_rules": ["([bad"]}, f)
        rc24b = run(chg("c24b.json", [{"path": "t.js", "status": "M"}]),
                    wr("d24b.txt", hdr("t.js") + "+ok();\n"))
        assert rc24b == NOT_WIRED, "expected 42 (invalid extra rule), got %d" % rc24b
        os.environ.pop("GOVERNANCE_DIR", None)

        # 场景25：.md 散文豁免（示例代码不误报），但文件仍须被 diff 覆盖
        rc25 = run(chg("c25.json", [{"path": "docs/guide.md", "status": "M"}]),
                   wr("d25.txt", hdr("docs/guide.md") + "+example: `assert True`\n"))
        assert rc25 == OK, "expected PASS (prose exempt), got %d" % rc25

        # 场景26：多文件 diff 归因不串（hunk 内 + 行各归各文件）
        d26 = wr("d26.txt",
                 hdr("a.js") + "@@ -1 +1,2 @@\n+ok()\n" +
                 hdr("b.js") + "@@ -1 +1,2 @@\n+it.skip('in-b')\n")
        rc26 = run(chg("c26.json", [{"path": "a.js", "status": "M"},
                                    {"path": "b.js", "status": "M"}]), d26)
        assert rc26 == VIOLATION, "expected VIOLATION (skip in b.js), got %d" % rc26
        kinds = [(f[2], f[3]) for f in _last]
        assert ("b.js", 1) in kinds and all(p == "b.js" for p, _ in kinds), \
            "finding must be attributed to b.js only: %s" % kinds

        # ---- v12：多行空 catch / suppress / 全豁免警告 ----
        # 场景27：except X: 换行 pass（black/ruff 格式化形态）-> 违规
        rc27 = run(chg("c27.json", [{"path": "p.py", "status": "M"}]),
                   wr("d27.txt", hdr("p.py") + "+try:\n+    risky()\n"
                      "+except Exception:\n+    pass\n"))
        assert rc27 == VIOLATION, \
            "expected VIOLATION (two-line empty except), got %d" % rc27

        # 场景28：except X: 换行 ...（Ellipsis 空体）-> 违规
        rc28 = run(chg("c28.json", [{"path": "p.py", "status": "M"}]),
                   wr("d28.txt", hdr("p.py") + "+except ValueError:\n+    ...\n"))
        assert rc28 == VIOLATION, \
            "expected VIOLATION (two-line ellipsis body), got %d" % rc28

        # 场景29：contextlib.suppress / with suppress -> 违规
        for snip in ("+with contextlib.suppress(Exception):\n+    risky()\n",
                     "+from contextlib import suppress\n"
                     "+with suppress(OSError):\n+    risky()\n"):
            d29 = wr("d29.txt", hdr("p.py") + snip)
            rc29 = run(chg("c29.json", [{"path": "p.py", "status": "M"}]), d29)
            assert rc29 == VIOLATION, \
                "expected VIOLATION (suppress), got %d" % rc29

        # 场景30：except 头与空体夹【新增】注释行 -> 检出（v13 对齐保守方向）
        rc30 = run(chg("c30.json", [{"path": "p.py", "status": "M"}]),
                   wr("d30.txt", hdr("p.py") + "+except Exception:\n"
                      "+    # TODO: handle\n+    pass\n"))
        assert rc30 == VIOLATION, \
            "expected VIOLATION (added comment between, v13), got %d" % rc30

        # 场景30b：except 头与空体夹【上下文】注释行 -> 同样检出（方向对齐）
        rc30b = run(chg("c30b.json", [{"path": "p.py", "status": "M"}]),
                    wr("d30b.txt", hdr("p.py") + "@@ -1,3 +1,4 @@\n"
                       "+except Exception:\n     # todo\n+    pass\n"))
        assert rc30b == VIOLATION, \
            "expected VIOLATION (context comment between, v13), got %d" % rc30b

        # 场景30c：注释后跟真实业务体 -> 不误报（非空 catch）
        rc30c = run(chg("c30c.json", [{"path": "p.py", "status": "M"}]),
                    wr("d30c.txt", hdr("p.py") + "+except Exception:\n"
                       "+    # cleanup\n+    cleanup()\n"))
        assert rc30c == OK, \
            "expected PASS (comment then real body, not empty catch), got %d" % rc30c

        # 场景31：非空 except 体不误报
        rc31 = run(chg("c31.json", [{"path": "p.py", "status": "M"}]),
                   wr("d31.txt", hdr("p.py") + "+except Exception:\n+    cleanup()\n"))
        assert rc31 == OK, "expected PASS (non-empty except body), got %d" % rc31

        # 场景32：allowlist ['.*'] 全豁免 -> 豁免生效，但 stderr 必须有 WARNING
        bd5 = os.path.join(tmp, "gov5")
        os.makedirs(bd5, exist_ok=True)
        with open(os.path.join(bd5, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"static_risk_allowlist": [".*"]}, f)
        os.environ["GOVERNANCE_DIR"] = bd5
        err32 = io.StringIO()
        with contextlib.redirect_stderr(err32):
            rc32 = run(chg("c32.json", [{"path": "t.py", "status": "M"}]),
                       wr("d32.txt", hdr("t.py") + "+it.skip('x')\n"))
        assert rc32 == OK, "expected PASS (everything allowlisted), got %d" % rc32
        assert "matches the empty string" in err32.getvalue(), \
            "expected broad-allowlist WARNING on stderr: %r" % err32.getvalue()
        os.environ.pop("GOVERNANCE_DIR", None)

        print("SELF-TEST: static_risk_check.py PASS (34 scenarios + 4 sub-asserts).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if old_gov is None:
            os.environ.pop("GOVERNANCE_DIR", None)
        else:
            os.environ["GOVERNANCE_DIR"] = old_gov


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
