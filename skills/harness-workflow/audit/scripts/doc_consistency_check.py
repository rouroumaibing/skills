#!/usr/bin/env python3
"""doc_consistency_check.py —— R-PRC-007 实装（文档一致性机检）。

审什么（对 --root 下的文档树，默认当前目录；--governance-docs 限定范围）：
  1. 死链：Markdown 相对链接与文内相对路径引用——先剥离 fenced code block
     （``` / ~~~ 围栏内的格式示例不算链接，历史教训），再逐条解析存在性；
  2. 修订痕迹：未解决的冲突标记 `<<<<<<<` / `>>>>>>>` / `|||||||`，
     以及 >=7 个等号的分隔行（markdown 误伤面已排除围栏内容）；
  3. 孤儿脚本：脚本目录（默认 audit/scripts）中未被任何 md 引用的文件
     （self-test / __pycache__ 豁免；孤儿 = 代码与文档漂移的信号）；
  4. 孤儿文档：references/ 下未被 SKILL.md 或其他 md 引用的文件。

退出码：0 通过 / 1 违反（发现死链/痕迹/孤儿）/ 42 未接线（root 不存在）。
已知边界：链接锚点（#fragment）不校验；外部 http(s) 链接不校验可达性。

用法：
  doc_consistency_check.py [--root <dir>] [--scripts-dir <dir>] [--self-test]
"""
import os
import sys
import io
import re
import tempfile
import shutil

VIOLATION = 1
OK = 0
NOT_WIRED = 42

_last = []


def _strip_fenced(text):
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"~~~.*?~~~", "", text, flags=re.S)


def _md_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "node_modules")]
        for fn in filenames:
            if fn.endswith(".md"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def check(root, scripts_dir=None):
    findings = []
    if not os.path.isdir(root):
        print("doc_consistency_check: root not found: %s" % root, file=sys.stderr)
        sys.exit(NOT_WIRED)
    mds = _md_files(root)
    all_text = {p: _strip_fenced(open(p, encoding="utf-8").read()) for p in mds}

    # 1) 死链：md 内相对路径引用（markdown link + 反引号路径两种形态）
    for p, text in all_text.items():
        base = os.path.dirname(p)
        targets = set()
        for m in re.finditer(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
            targets.add(m.group(1))
        for m in re.finditer(r"`((?:references|audit)/[\w/.\-]+)`", text):
            targets.add(m.group(1))
        for t in targets:
            if re.match(r"https?://", t) or "*" in t or "<" in t or t.endswith("/"):
                continue
            t = t.rstrip(".,;)。；）")
            cand = os.path.normpath(os.path.join(base, t))
            alt = os.path.normpath(os.path.join(root, t))  # skill 根相对
            if not os.path.exists(cand) and not os.path.exists(alt):
                findings.append("dead-link in %s -> %s" % (os.path.relpath(p, root), t))

    # 2) 修订痕迹
    for p, text in all_text.items():
        for i, ln in enumerate(text.splitlines(), 1):
            if re.match(r"^(<{7}|>{7}|\|{7})", ln) or re.match(r"^={7,}$", ln):
                findings.append("conflict marker in %s:%d: %s"
                                % (os.path.relpath(p, root), i, ln[:40]))

    # 3) 孤儿脚本
    if scripts_dir and os.path.isdir(scripts_dir):
        joined = "\n".join(all_text.values())
        for fn in sorted(os.listdir(scripts_dir)):
            if fn.startswith("__") or fn == "self-test":
                continue
            if fn not in joined:
                findings.append("orphan script (not referenced by any md): %s/%s"
                                % (os.path.relpath(scripts_dir, root), fn))

    # 4) 孤儿文档（references/ 下未被任何其他 md 引用）
    refs_dir = os.path.join(root, "references")
    if os.path.isdir(refs_dir):
        joined = "\n".join(all_text.values())
        for fn in sorted(os.listdir(refs_dir)):
            if not fn.endswith(".md") or fn == "README.md":
                continue
            others = "\n".join(t for p, t in all_text.items()
                               if os.path.basename(p) != fn)
            if fn not in others:
                findings.append("orphan doc (not referenced by any other md): "
                                "references/%s" % fn)

    _last.clear()
    _last.extend(findings)
    return findings


def main(argv):
    if "--self-test" in argv:
        return self_test()
    root = os.getcwd()
    scripts_dir = None
    for i, a in enumerate(argv):
        if a == "--root":
            root = argv[i + 1]
        elif a == "--scripts-dir":
            scripts_dir = argv[i + 1]
    findings = check(root, scripts_dir)
    if findings:
        for f in findings:
            print("DOC-ISSUE " + f, file=sys.stderr)
        print("doc_consistency_check: %d finding(s) -> VIOLATION (R-PRC-007)."
              % len(findings), file=sys.stderr)
        return VIOLATION
    print("doc_consistency_check: PASS (no dead links / conflict markers / "
          "orphans).")
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="doc-cons-")
    try:
        def wr(rel, text):
            p = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return p

        # 场景1：干净树 -> 0
        wr("SKILL.md", "see [rules](references/rules.md) and `audit/scripts/c.py`\n")
        wr("references/rules.md", "ok\n")
        os.makedirs(os.path.join(tmp, "audit", "scripts"), exist_ok=True)
        wr("audit/scripts/c.py", "#!/usr/bin/env python3\n")
        findings = check(tmp, os.path.join(tmp, "audit", "scripts"))
        assert not findings, "expected clean, got %s" % findings

        # 场景2：死链（围栏内不算）-> 1
        wr("references/rules.md",
           "```markdown\n[example](references/nope.md)\n```\n"
           "real link: [x](references/gone.md)\n")
        findings = check(tmp)
        assert any("gone.md" in f for f in findings), findings
        assert not any("nope.md" in f for f in findings), \
            "fenced example must not be scanned: %s" % findings

        # 场景3：冲突标记 -> 1
        wr("references/rules.md", "good\n<<<<<<< HEAD\nmine\n>>>>>>> other\n")
        findings = check(tmp)
        assert any("conflict marker" in f for f in findings), findings

        # 场景4：孤儿脚本 -> 1
        wr("references/rules.md", "ok\n")
        wr("audit/scripts/lonely.py", "#!/usr/bin/env python3\n")
        findings = check(tmp, os.path.join(tmp, "audit", "scripts"))
        assert any("lonely.py" in f for f in findings), findings

        # 场景5：孤儿文档 -> 1
        wr("references/forgotten.md", "nobody links me\n")
        findings = check(tmp)
        assert any("forgotten.md" in f for f in findings), findings

        # 场景6：root 不存在 -> 42
        try:
            check(os.path.join(tmp, "no-such-dir"))
            rc = OK
        except SystemExit as e:
            rc = e.code
        assert rc == NOT_WIRED, "expected 42 (missing root), got %d" % rc

        # 场景7：被引用脚本不再是孤儿（引用来自引用它的 md 即可）
        wr("SKILL.md", "use `audit/scripts/lonely.py`\n")
        findings = check(tmp, os.path.join(tmp, "audit", "scripts"))
        assert not any("lonely.py" in f for f in findings), findings

        print("SELF-TEST: doc_consistency_check.py PASS (7 scenarios).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
