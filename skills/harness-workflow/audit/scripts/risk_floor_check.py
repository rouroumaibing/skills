#!/usr/bin/env python3
r"""risk_floor_check.py —— R-RISK-001 实装（合入必需检查）。

服务规则 R-RISK-001：工程从 diff 按 redlines.yaml 的 risk_patterns 推算风险下限；
实际等级高于契约声明时触发升级 E-17。

推算口径（保守）：diff 命中任一 risk_pattern（受控路径 path_glob 或受控 SQL 模式 pattern）
=> 推算下限 = high（auth/payment/migration/secrets/不可逆DDL 一律按最高风险对待）。
命中后若 contract_risk_level 低于 high（即 low 或 medium），视为"声明低于推算下限"，
退出非零触发 E-17。

匹配语义与已知盲区（报告须注明"机器推算仅供参考"）：
  - path_glob 支持 ** 跨目录段（fnmatch 原生不支持）；匹配大小写不敏感（防大小写绕过）。
    尾部 `**` 为目录语义：`**/auth/**` 只命中 auth 目录下的文件，不命中名为 auth 的文件本身。
  - 内容级 pattern 的路径范围用两个可选字段（互不冲突，可只用其一）：
      skip_paths  黑名单：跳过命中的文件，其余全扫（默认推荐——白名单会漏检
                  未列入的扩展名，如 .rs/.kt/.cs/.xml/变更 yaml）；
      only_paths  白名单：只扫命中的文件（需精确收紧时用）。
    旧写法把 path_glob 附在 pattern 上仍兼容（等价 only_paths）。diff 无文件头时
    无法归因，保守全扫（宁误报不漏报）。
  - 内容级 pattern 只扫 diff 新增行（"+" 开头，排除 "+++"）——被删行不计，删除不是新增风险。
  - 相邻新增行（中间无上下文/删除行）拼接后折叠空白再扫：单行内 "DROP   TABLE"
    与跨行拆分（"+DROP" 下一行 "+TABLE x"）可检出；不相邻的行不拼，防拼接出假命中。
  - 已知盲区（正则不可检出，靠人审与专业扫描器）：字符串/标识符拼接
    （'DR'+'OP TABLE'）、运行时动态构造 SQL、编码变形、相邻两行各含半句的巧合
    （如注释里 "may DROP" 下一行 "TABLE of contents"——宁误报不漏报）、
    相邻新增行之间夹一条新增注释行（"+DROP" / "+-- c" / "+TABLE x"：注释行
    同属拼接串，其文本把两个半句隔开，"DROP\s+TABLE" 匹配不上——漏报，
    归人审）；建议项目
    在 risk_patterns 按语言特征自补（starter 已含 '\\bDELETE\\s+FROM\\b' 默认规则，
    配合人核 WHERE）。

用法：
  risk_floor_check.py --changes <changes.json> --diff <diff.txt> --contract-risk-level <low|medium|high> [--repo <path>]
  risk_floor_check.py --self-test

changes.json / diff.txt 来自本次 PR 相对 base 的变更（CI 产出）。
"""
import os
import sys
import io
import json
import re
import difflib
import tempfile
import shutil
import fnmatch

try:
    import yaml
except ImportError:
    yaml = None

GOVERNANCE_DIR = os.environ.get("GOVERNANCE_DIR",
                                os.path.dirname(os.path.abspath(__file__)).rstrip("/") + "/..")
REDLINES = os.path.join(GOVERNANCE_DIR, "redlines.yaml")
VIOLATION = 1
OK = 0
NOT_WIRED = 42  # 判据来源缺失/损坏（fail-closed）
ORDER = {"low": 0, "medium": 1, "high": 2}
_last_hits = []


KNOWN_ENTRY_KEYS = ("pattern", "path_glob", "skip_paths", "only_paths")


def load_redlines():
    if yaml is None:
        print("risk_floor_check: pyyaml required (pip install pyyaml).", file=sys.stderr)
        sys.exit(42)
    path = os.path.join(os.environ.get("GOVERNANCE_DIR", GOVERNANCE_DIR), "redlines.yaml")
    if not os.path.exists(path):
        print("risk_floor_check: redlines.yaml not found: " + path, file=sys.stderr)
        sys.exit(42)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        # 判据来源损坏：不能当"通过"也不能误读为"违规"，按未实装退出（fail-closed）
        print("risk_floor_check: redlines.yaml is corrupt (not valid YAML); "
              "treat as NOT-WIRED and fix the file. Path: %s\n  %s" % (path, e),
              file=sys.stderr)
        sys.exit(42)

    def not_wired(msg):
        print("risk_floor_check: redlines.yaml has no usable machine criteria "
              "(%s); risk floor NOT evaluated, treat as NOT-WIRED (fail-closed, "
              "report as manual review). Path: %s" % (msg, path), file=sys.stderr)
        sys.exit(42)

    # 配置有效性校验（D-01 同类原则：没有证据 = 未评估，不得输出 PASS）：
    # 语法合法但零有效规则（键缺失 / 空列表 / 类型错 / 条目缺 pattern 与 path_glob /
    # 空文件 / 非法正则）一律退出 42，不静默通过。
    if data is None or not isinstance(data, dict):
        not_wired("file is empty or not a YAML mapping")
    pats = data.get("risk_patterns")
    if pats is None:
        not_wired("missing 'risk_patterns' key")
    if not isinstance(pats, list):
        not_wired("'risk_patterns' is not a list (got %s)" % type(pats).__name__)
    def _glob_specs(v):
        """glob 类字段值校验：非空字符串或非空字符串列表 → 字符串列表；否则 None。"""
        if isinstance(v, str):
            return [v] if v.strip() else None
        if isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v):
            return v
        return None

    valid = 0
    for ent in pats:
        if not isinstance(ent, dict):
            not_wired("risk_patterns entry is not a mapping: %r" % (ent,))
        # 无法识别键警告（含近似提示）——对每类条目都查，不只缺主体的条目
        for k in ent:
            if k not in KNOWN_ENTRY_KEYS:
                hint = difflib.get_close_matches(k, KNOWN_ENTRY_KEYS, n=1)
                print("risk_floor_check: WARNING ignoring unrecognized key %r in "
                      "risk_patterns entry%s" % (k, " (did you mean %r?)" % hint[0] if hint else ""),
                      file=sys.stderr)
        if "pattern" in ent:
            p = ent["pattern"]
            if not isinstance(p, str) or not p.strip():
                not_wired("'pattern' must be a non-empty string (got %r)" % (p,))
            try:
                re.compile(p, re.IGNORECASE)
            except (re.error, TypeError) as e:
                not_wired("invalid regex in risk_patterns: %r (%s)" % (p, e))
            valid += 1
        elif "path_glob" in ent:
            if _glob_specs(ent["path_glob"]) is None:
                not_wired("'path_glob' must be a non-empty string or non-empty list "
                          "of strings (got %r) — an empty scope evaluates no paths" % (ent["path_glob"],))
            valid += 1
        elif "skip_paths" in ent or "only_paths" in ent:
            # 合法键但缺主体字段：评估不了任何东西，措辞要指向缺什么，而不是键名不认识
            for k in ("skip_paths", "only_paths"):
                if k in ent and _glob_specs(ent[k]) is None:
                    not_wired("%r must be a non-empty string or non-empty list of "
                              "strings (got %r)" % (k, ent[k]))
            not_wired("entry has scope key(s) %s but no 'pattern' or 'path_glob' — "
                      "a scope alone evaluates nothing (did you forget the 'pattern' "
                      "or 'path_glob' field?)" % ([k for k in ent if k in ("skip_paths", "only_paths")],))
    if valid == 0:
        not_wired("zero usable entries (every entry lacks 'pattern' and 'path_glob')")
    return data


def _match_segs(segs, pats):
    """逐段匹配：** 匹配零或多段，其余段走 fnmatch（段内 * 不跨 /）。"""
    if not pats:
        return not segs
    head = pats[0]
    if head == "**":
        rest = pats[1:]
        if not rest:
            # 尾部 ** = 目录语义：至少还剩一段（文件名）才算命中，
            # 防 "**/auth/**" 命中名为 auth 的文件本身（轻微误报）。
            return bool(segs)
        return _match_segs(segs, rest) or (bool(segs) and _match_segs(segs[1:], pats))
    if not segs:
        return False
    if fnmatch.fnmatch(segs[0], head):
        return _match_segs(segs[1:], pats[1:])
    return False


def _glob_match(path, pattern):
    """双星感知的 glob 匹配；大小写不敏感（防大小写变体绕过受控路径）。"""
    return _match_segs(path.lower().split("/"), pattern.lower().split("/"))


def _glob_list(v):
    """path_glob/only_paths/skip_paths 字段归一为列表（兼容单个字符串写法）。"""
    if v is None:
        return None
    return v if isinstance(v, list) else [v]


def _diff_added(diff_text):
    """解析 diff，返回 (by_file, has_headers, all_runs)。

    by_file: {path: [run, ...]}，按 `+++ b/<path>` 头归因；
    has_headers: diff 是否含文件头；all_runs: 全部 run（含无头部分）。
    run = 相邻的新增行（中间无上下文/删除行打断）——跨行拼接只作用于同一 run，
    不相隔的行不拼，防拼接出假命中。
    """
    by_file = {}
    has_headers = False
    all_runs = []
    cur = None        # 当前归因文件（等 +++ 头）
    run = []
    run_file = None

    def flush():
        nonlocal run
        if run:
            if run_file is not None:
                by_file.setdefault(run_file, []).append(run)
            all_runs.append(run)
            run = []

    for ln in (diff_text or "").splitlines():
        if ln.startswith("+++ "):
            flush()
            has_headers = True
            p = ln[4:].strip()
            if p.startswith("b/"):
                p = p[2:]
            cur = None if p in ("", "/dev/null") else p.replace(os.sep, "/")
        elif ln.startswith("diff --git "):
            flush()
            cur = None  # 等下一个 +++ 头再归因
        elif ln.startswith("+") and not ln.startswith("+++"):
            if run and run_file == cur:
                run.append(ln)
            else:
                flush()
                run = [ln]
                run_file = cur
        else:
            flush()  # 上下文/删除/其他行打断 run
    flush()
    return by_file, has_headers, all_runs


def _fold(text):
    """折叠空白（空格/tab 连续段压成单空格）。"""
    return re.sub(r"[ \t]+", " ", text)


def _scan_runs(rx, runs):
    """逐 run 扫描：先逐行，再拼相邻行（折叠空白，含换行）。返回命中描述或 None。"""
    for run in runs:
        for ln in run:
            if rx.search(_fold(ln)):
                return ln[:60]
        if run:
            joined = _fold(" ".join(x[1:].strip() for x in run))
            if rx.search(joined):
                return "<joined> " + joined[:53]
    return None


def hits(redlines, changes, diff_text):
    out = []
    patterns = redlines.get("risk_patterns", []) or []
    changed_paths = []
    for ch in changes:
        p = ch.get("path") or ch.get("old_path")
        if p:
            changed_paths.append(p.replace(os.sep, "/"))
    for pat in patterns:
        if "pattern" in pat:
            # 内容模式：skip_paths 黑名单 / only_paths 白名单限定路径范围；
            # pattern 优先判定，防 pattern+path_glob 组合被误当成纯路径规则。
            rx = re.compile(pat["pattern"], re.IGNORECASE)
            only_paths = _glob_list(pat.get("only_paths"))
            if only_paths is None:
                only_paths = _glob_list(pat.get("path_glob"))  # 旧字段名，兼容
            skip_paths = _glob_list(pat.get("skip_paths"))
            by_file, has_headers, all_runs = _diff_added(diff_text)
            hit = None
            if not has_headers:
                # 无文件头（无法归因）：保守全扫（宁误报不漏报）。
                hit = _scan_runs(rx, all_runs)
            else:
                for path, runs in by_file.items():
                    if skip_paths and any(_glob_match(path, g) for g in skip_paths):
                        continue
                    if only_paths and not any(_glob_match(path, g) for g in only_paths):
                        continue
                    hit = _scan_runs(rx, runs)
                    if hit is not None:
                        hit = "%s: %s" % (path, hit)
                        break
            if hit is not None:
                out.append(("pattern", pat["pattern"], hit))
        elif "path_glob" in pat:
            # 纯路径规则：命中受控路径即按 high 推算（不带 pattern 时才走这里）。
            g = pat["path_glob"]
            globs = _glob_list(g)
            for p in changed_paths:
                if any(_glob_match(p, x) for x in globs):
                    out.append(("path_glob", g, p))
    return out


def main(argv):
    changes_path = diff_path = contract_level = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--changes":
            changes_path = argv[i + 1]; i += 2; continue
        elif a == "--diff":
            diff_path = argv[i + 1]; i += 2; continue
        elif a == "--contract-risk-level":
            contract_level = argv[i + 1]; i += 2; continue
        elif a == "--self-test":
            return self_test()
        i += 1
    if None in (changes_path, diff_path, contract_level):
        print("risk_floor_check: --changes/--diff/--contract-risk-level required.", file=sys.stderr)
        return 42
    if contract_level not in ORDER:
        print("risk_floor_check: bad --contract-risk-level %r" % contract_level, file=sys.stderr)
        return 42

    redlines = load_redlines()
    with open(changes_path, encoding="utf-8") as f:
        changes = json.load(f)
    with open(diff_path, encoding="utf-8", errors="replace") as f:
        diff_text = f.read()

    found = hits(redlines, changes, diff_text)
    _last_hits.clear()
    _last_hits.extend(found)

    if not found:
        print("risk_floor_check: PASS (no risk_pattern hit; floor=low).")
        return OK

    floor = "high"  # 命中受控模式一律按最高风险推算
    printed_floor = floor
    if ORDER[floor] > ORDER[contract_level]:
        for kind, spec, where in found:
            print("RISK-FLOOR[R-RISK-001] hit %s=%s at %s => floor=%s > declared=%s"
                  % (kind, spec, where, printed_floor, contract_level), file=sys.stderr)
        print("risk_floor_check: VIOLATION (declared %s below inferred floor %s) -> escalate E-17."
              % (contract_level, printed_floor), file=sys.stderr)
        return VIOLATION
    print("risk_floor_check: PASS (declared %s >= floor %s)." % (contract_level, printed_floor))
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="rfc-test-")
    try:
        redlines = {"risk_patterns": [
            {"path_glob": "**/auth/**"},
            {"pattern": "GRANT|REVOKE|ALTER TABLE"},
            {"pattern": "DROP TABLE"},
        ]}
        with open(os.path.join(tmp, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(redlines, f, allow_unicode=True)

        # 场景1：命中 auth 路径、契约声明 low -> 触发
        ch = os.path.join(tmp, "c.json")
        with open(ch, "w", encoding="utf-8") as f:
            json.dump([{"path": "src/auth/login.py", "status": "A"}], f)
        d = os.path.join(tmp, "d.txt")
        open(d, "w", encoding="utf-8").close()
        os.environ["GOVERNANCE_DIR"] = tmp
        rc1 = main(["--changes", ch, "--diff", d, "--contract-risk-level", "low"])
        assert rc1 == VIOLATION, "expected VIOLATION, got %d" % rc1
        assert any(k == "path_glob" for k, _, _ in _last_hits), _last_hits

        # 场景2：命中 DDL 模式、契约 medium -> 触发
        ch2 = os.path.join(tmp, "c2.json")
        with open(ch2, "w", encoding="utf-8") as f:
            json.dump([{"path": "src/db/seed.py", "status": "M"}], f)
        d2 = os.path.join(tmp, "d2.txt")
        with open(d2, "w", encoding="utf-8") as f:
            f.write("+ALTER TABLE users ADD COLUMN x int;\n")
        rc2 = main(["--changes", ch2, "--diff", d2, "--contract-risk-level", "medium"])
        assert rc2 == VIOLATION, "expected VIOLATION (DDL), got %d" % rc2

        # 场景3：无命中、契约 low -> PASS
        ch3 = os.path.join(tmp, "c3.json")
        with open(ch3, "w", encoding="utf-8") as f:
            json.dump([{"path": "src/util/format.py", "status": "A"}], f)
        d3 = os.path.join(tmp, "d3.txt")
        open(d3, "w", encoding="utf-8").close()
        rc3 = main(["--changes", ch3, "--diff", d3, "--contract-risk-level", "low"])
        assert rc3 == OK, "expected PASS, got %d" % rc3

        # 场景4：命中但契约 high -> PASS（声明已是最高的）
        rc4 = main(["--changes", ch, "--diff", d, "--contract-risk-level", "high"])
        assert rc4 == OK, "expected PASS (declared high == floor), got %d" % rc4

        # 场景5：顶层 auth/ 也必须命中（RT-13 防线，历史漏洞）
        ch5 = os.path.join(tmp, "c5.json")
        with open(ch5, "w", encoding="utf-8") as f:
            json.dump([{"path": "auth/login.py", "status": "A"}], f)
        rc5 = main(["--changes", ch5, "--diff", d3, "--contract-risk-level", "low"])
        assert rc5 == VIOLATION, "expected VIOLATION (top-level auth/), got %d" % rc5

        # 场景6：大小写变体不构成绕过（匹配大小写不敏感）
        ch6 = os.path.join(tmp, "c6.json")
        with open(ch6, "w", encoding="utf-8") as f:
            json.dump([{"path": "src/Auth/x.py", "status": "A"}], f)
        rc6 = main(["--changes", ch6, "--diff", d3, "--contract-risk-level", "low"])
        assert rc6 == VIOLATION, "expected VIOLATION (case variant), got %d" % rc6

        # 场景7：空白折叠防 "DROP   TABLE" 绕过
        ch7 = os.path.join(tmp, "c7.json")
        with open(ch7, "w", encoding="utf-8") as f:
            json.dump([{"path": "src/db/seed.py", "status": "A"}], f)
        d7 = os.path.join(tmp, "d7.txt")
        with open(d7, "w", encoding="utf-8") as f:
            f.write("+DROP   TABLE users;\n")
        rc7 = main(["--changes", ch7, "--diff", d7, "--contract-risk-level", "low"])
        assert rc7 == VIOLATION, "expected VIOLATION (whitespace), got %d" % rc7

        # 场景8：被删行不算新增风险（只扫 "+" 行）
        d8 = os.path.join(tmp, "d8.txt")
        with open(d8, "w", encoding="utf-8") as f:
            f.write("-ALTER TABLE users DROP COLUMN x;\n")
        rc8 = main(["--changes", ch7, "--diff", d8, "--contract-risk-level", "low"])
        assert rc8 == OK, "expected PASS (deleted line not scanned), got %d" % rc8

        # 场景9：跨行拆分（相邻 +DROP 下一行 +TABLE x）可检出（相邻行拼接）
        d9 = os.path.join(tmp, "d9.txt")
        with open(d9, "w", encoding="utf-8") as f:
            f.write("+DROP\n+TABLE users;\n")
        rc9 = main(["--changes", ch7, "--diff", d9, "--contract-risk-level", "low"])
        assert rc9 == VIOLATION, "expected VIOLATION (cross-line split), got %d" % rc9

        # 场景10：名为 auth 的文件（a/b/auth）不被 **/auth/** 命中（目录语义）
        ch10 = os.path.join(tmp, "c10.json")
        with open(ch10, "w", encoding="utf-8") as f:
            json.dump([{"path": "a/b/auth", "status": "A"}], f)
        rc10 = main(["--changes", ch10, "--diff", d3, "--contract-risk-level", "low"])
        assert rc10 == OK, "expected PASS (file named auth not a dir), got %d" % rc10

        # 场景11~16：skip_paths 黑名单 / only_paths 白名单 / 相邻行语义
        redlines2 = {"risk_patterns": [
            {"pattern": "\\b(GRANT|REVOKE)\\b",
             "skip_paths": ["**/*.md", "**/*.txt", "**/*.rst", "docs/**"]},
            {"pattern": "\\bDELETE\\s+FROM\\b",
             "only_paths": ["**/*.sql"]},
            {"pattern": "\\b(ALTER|DROP)\\s+TABLE\\b", "path_glob": ["**/*.sql"]},  # 旧字段名兼容
        ]}
        with open(os.path.join(tmp, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(redlines2, f, allow_unicode=True)

        def chg(name, path):
            p = os.path.join(tmp, name)
            with open(p, "w", encoding="utf-8") as f:
                json.dump([{"path": path, "status": "A"}], f)
            return p

        def wr(name, text):
            p = os.path.join(tmp, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return p

        # 场景11：黑名单跳过 .md 散文 "We grant you a license" -> 不误报
        d11 = wr("d11.txt", "diff --git a/docs/license.md b/docs/license.md\n"
                            "+++ b/docs/license.md\n"
                            "+We grant you a license to use this software.\n")
        rc11 = main(["--changes", chg("c11.json", "docs/license.md"),
                     "--diff", d11, "--contract-risk-level", "low"])
        assert rc11 == OK, "expected PASS (prose skipped by skip_paths), got %d" % rc11

        # 场景12：黑名单下非常见扩展名（.rs）不再漏检（白名单历史缺口）
        d12 = wr("d12.txt", "diff --git a/src/x.rs b/src/x.rs\n"
                            "+++ b/src/x.rs\n"
                            "+GRANT SELECT ON users TO app;\n")
        rc12 = main(["--changes", chg("c12.json", "src/x.rs"),
                     "--diff", d12, "--contract-risk-level", "low"])
        assert rc12 == VIOLATION, "expected VIOLATION (.rs no longer missed), got %d" % rc12

        # 场景13：变更 yaml（Liquibase 类）同理不再漏检（黑名单不含 yaml）
        d13 = wr("d13.txt", "diff --git a/db/changes.yaml b/db/changes.yaml\n"
                            "+++ b/db/changes.yaml\n"
                            "+- changeSet:\n"
                            "+    sql: GRANT SELECT ON users TO app;\n")
        rc13 = main(["--changes", chg("c13.json", "db/changes.yaml"),
                     "--diff", d13, "--contract-risk-level", "low"])
        assert rc13 == VIOLATION, "expected VIOLATION (changes.yaml no longer missed), got %d" % rc13

        # 场景14：only_paths 白名单收紧生效——.py 里 DELETE 不扫，.sql 里命中
        d14 = wr("d14.txt", "diff --git a/src/x.py b/src/x.py\n"
                            "+++ b/src/x.py\n"
                            "+DELETE FROM users;\n")
        rc14 = main(["--changes", chg("c14.json", "src/x.py"),
                     "--diff", d14, "--contract-risk-level", "low"])
        assert rc14 == OK, "expected PASS (only_paths excludes .py), got %d" % rc14

        # 场景15：旧字段名 path_glob 附在 pattern 上仍兼容（等价 only_paths）
        d15 = wr("d15.txt", "diff --git a/src/x.py b/src/x.py\n"
                            "+++ b/src/x.py\n"
                            "+ALTER TABLE users;\n")
        rc15 = main(["--changes", chg("c15.json", "src/x.py"),
                     "--diff", d15, "--contract-risk-level", "low"])
        assert rc15 == OK, "expected PASS (legacy path_glob = only_paths), got %d" % rc15
        d15b = wr("d15b.txt", "diff --git a/db/x.sql b/db/x.sql\n"
                              "+++ b/db/x.sql\n"
                              "+ALTER TABLE users;\n")
        rc15b = main(["--changes", chg("c15b.json", "db/x.sql"),
                      "--diff", d15b, "--contract-risk-level", "low"])
        assert rc15b == VIOLATION, "expected VIOLATION (legacy path_glob .sql), got %d" % rc15b

        # 场景16：拼接只限相邻新增行——中间隔上下文行不拼（跨 hunk 不再误拼）
        d16 = wr("d16.txt", "diff --git a/src/x.py b/src/x.py\n"
                            "+++ b/src/x.py\n"
                            "+DROP\n"
                            " context line\n"
                            "+TABLE users;\n")
        rc16 = main(["--changes", chg("c16.json", "src/x.py"),
                     "--diff", d16, "--contract-risk-level", "low"])
        assert rc16 == OK, "expected PASS (non-adjacent lines not joined), got %d" % rc16

        # 场景17：redlines.yaml 损坏（非法 YAML）→ 判据来源损坏，退出 42（fail-closed），
        # 既不当"通过"也不误读为"违规"
        bad_gov = os.path.join(tmp, "bad-gov")
        os.makedirs(bad_gov, exist_ok=True)
        with open(os.path.join(bad_gov, "redlines.yaml"), "w", encoding="utf-8") as f:
            f.write("risk_patterns: [broken\n  - :: {{{yaml")
        old_gov = os.environ.get("GOVERNANCE_DIR")
        os.environ["GOVERNANCE_DIR"] = bad_gov
        try:
            rc17 = main(["--changes", chg("c17.json", "db/x.sql"),
                         "--diff", d16, "--contract-risk-level", "low"])
        except SystemExit as e:
            rc17 = e.code
        finally:
            if old_gov is None:
                os.environ.pop("GOVERNANCE_DIR", None)
            else:
                os.environ["GOVERNANCE_DIR"] = old_gov
        assert rc17 == NOT_WIRED, "expected 42 (corrupt redlines = not wired), got %d" % rc17

        # ---- 场景18~22：语法合法但零有效规则 → 42（没有规则被评估 ≠ PASS）----
        broken_cfgs = {
            "g-miss-key": "thresholds: {}\n",                       # 缺 risk_patterns 键
            "g-empty-list": "risk_patterns: []\n",                  # 空列表
            "g-typo-key": "risk_patterns:\n  - patern: 'x'\n",      # 条目字段拼错 → 零有效
            "g-empty-file": "",                                     # 空文件（原 AttributeError）
            "g-bad-regex": "risk_patterns:\n  - pattern: '(unclosed'\n",  # 非法正则（原 re.error）
        }
        # ---- 场景24~30：值类型无效（键存在但值不可用）→ 42（原 TypeError 崩溃 rc=1
        #      或静默 PASS rc=0，同属"没有规则被评估 ≠ PASS"）----
        broken_cfgs.update({
            "g-empty-pattern": "risk_patterns:\n  - pattern:\n",            # pattern 空（原 TypeError）
            "g-int-pattern": "risk_patterns:\n  - pattern: 123\n",          # pattern 非 str（原 TypeError）
            "g-list-pattern": "risk_patterns:\n  - pattern: ['DROP TABLE']\n",  # pattern 写成列表
            "g-blank-pattern": "risk_patterns:\n  - pattern: '   '\n",      # pattern 纯空白
            "g-empty-pathglob": "risk_patterns:\n  - path_glob: []\n",      # 空 glob 列表（原静默 PASS）
            "g-none-pathglob": "risk_patterns:\n  - path_glob:\n",          # glob 空（原 TypeError）
            "g-scope-only": "risk_patterns:\n  - skip_paths: ['**/*.md']\n",  # 只有范围键、缺主体
        })
        for name, cfg in broken_cfgs.items():
            bd = os.path.join(tmp, name)
            os.makedirs(bd, exist_ok=True)
            with open(os.path.join(bd, "redlines.yaml"), "w", encoding="utf-8") as f:
                f.write(cfg)
            old_gov = os.environ.get("GOVERNANCE_DIR")
            os.environ["GOVERNANCE_DIR"] = bd
            try:
                rc = main(["--changes", chg("c-%s.json" % name, "db/x.sql"),
                           "--diff", d16, "--contract-risk-level", "low"])
            except SystemExit as e:
                rc = e.code
            finally:
                if old_gov is None:
                    os.environ.pop("GOVERNANCE_DIR", None)
                else:
                    os.environ["GOVERNANCE_DIR"] = old_gov
            assert rc == NOT_WIRED, "expected 42 (%s = no usable criteria), got %d" % (name, rc)

        # 场景23：纯 path_glob 配置（无内容 pattern）是合法形态 → 正常评估，不触发 42
        pg = os.path.join(tmp, "g-path-only")
        os.makedirs(pg, exist_ok=True)
        with open(os.path.join(pg, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"risk_patterns": [{"path_glob": "**/auth/**"}]}, f)
        old_gov = os.environ.get("GOVERNANCE_DIR")
        os.environ["GOVERNANCE_DIR"] = pg
        try:
            rc23 = main(["--changes", chg("c23.json", "src/x.py"),
                         "--diff", d16, "--contract-risk-level", "low"])
        except SystemExit as e:
            rc23 = e.code
        finally:
            if old_gov is None:
                os.environ.pop("GOVERNANCE_DIR", None)
            else:
                os.environ["GOVERNANCE_DIR"] = old_gov
        assert rc23 == OK, "expected PASS (path-only config is valid), got %d" % rc23

        # 场景31：合法 pattern 条目上拼错 only_path → 不阻断（保守方向全扫），
        # 但必须打近似键名警告（did you mean 'only_paths'?），退出码不受警告影响
        wp = os.path.join(tmp, "g-typo-opt")
        os.makedirs(wp, exist_ok=True)
        with open(os.path.join(wp, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"risk_patterns": [
                {"pattern": r"\bDROP\s+TABLE\b", "only_path": "**/*.sql"}]}, f)
        old_gov = os.environ.get("GOVERNANCE_DIR")
        os.environ["GOVERNANCE_DIR"] = wp
        d31 = wr("d31.txt", "diff --git a/db/x.sql b/db/x.sql\n"
                            "+++ b/db/x.sql\n"
                            "+DROP TABLE users;\n")
        buf = io.StringIO()
        _real_stderr = sys.stderr
        sys.stderr = buf
        try:
            rc31 = main(["--changes", chg("c31.json", "db/x.sql"),
                         "--diff", d31, "--contract-risk-level", "low"])
        except SystemExit as e:
            rc31 = e.code
        finally:
            sys.stderr = _real_stderr
            if old_gov is None:
                os.environ.pop("GOVERNANCE_DIR", None)
            else:
                os.environ["GOVERNANCE_DIR"] = old_gov
        assert rc31 == VIOLATION, "expected VIOLATION (conservative full scan still hits), got %d" % rc31
        assert "only_path" in buf.getvalue() and "did you mean 'only_paths'?" in buf.getvalue(), \
            "expected close-match warning for typo'd 'only_path', got: %r" % buf.getvalue()

        print("SELF-TEST: risk_floor_check.py PASS (31 scenarios).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("GOVERNANCE_DIR", None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
