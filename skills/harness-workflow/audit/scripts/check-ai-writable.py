#!/usr/bin/env python3
"""check-ai-writable.py —— R-SEC-004 实装（合入必需检查）。

服务规则 R-SEC-004：`ai_writable_paths` 仅允许数据文件（md/json/yaml/yml/txt/csv），
单文件 <= `ai_writable_max_bytes`（默认 64KB），禁止符号链接与可执行内容。
合入前校验 ai_writable_paths 下新增/修改文件符合上述约束；其余路径不在本脚本职责内
（R-SEC-001 由服务端权限、R-IMP-001 由范围检查负责）。

用法：
  check-ai-writable.py --changes <changes.json> [--repo <path>] [--self-test]
  check-ai-writable.py --self-test

changes.json 格式（机器产出，git diff --name-status 解析而得）：
  [{"path":"ai-reports/x.md","status":"A"},
   {"path":"ai-reports/y.json","status":"M"},
   {"path":"ai-reports/old.md","old_path":"ai-reports/new.md","status":"R"}]
  status: A 新增 / M 修改 / D 删除（删除不校验内容）/ R 重命名（校验新路径）。
本脚本只校验"落在 ai_writable_paths 内"的文件；落在其外的变更交由 R-SEC-001 / R-IMP-001。
"""
import os
import sys
import json
import tempfile
import shutil

try:
    import yaml  # 消费项目需安装 pyyaml；缺失时给出明确提示而非静默放行
except ImportError:
    yaml = None

GOVERNANCE_DIR = os.environ.get("GOVERNANCE_DIR",
                                os.path.dirname(os.path.abspath(__file__)).rstrip("/") + "/..")
REPO_ROOT = os.environ.get("REPO_ROOT", os.path.dirname(os.path.abspath(GOVERNANCE_DIR)))
REDLINES = os.path.join(GOVERNANCE_DIR, "redlines.yaml")

ALLOWED_EXT = {".md", ".json", ".yaml", ".yml", ".txt", ".csv"}
VIOLATION = 1
OK = 0

# 校验结果（全局，便于 self-test 断言）
_last_violations = []


def load_redlines():
    if yaml is None:
        print("check-ai-writable: pyyaml required (pip install pyyaml).", file=sys.stderr)
        sys.exit(42)
    path = os.path.join(os.environ.get("GOVERNANCE_DIR", GOVERNANCE_DIR), "redlines.yaml")
    if not os.path.exists(path):
        print("check-ai-writable: redlines.yaml not found: " + path, file=sys.stderr)
        sys.exit(42)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def max_bytes(redlines):
    try:
        return int(redlines.get("thresholds", {}).get("ai_writable_max_bytes", 65536))
    except (TypeError, ValueError):
        return 65536


def under(path, prefixes):
    p = path.replace(os.sep, "/")
    for pref in prefixes:
        pref = pref.rstrip("/")
        if p == pref or p.startswith(pref + "/"):
            return True
    return False


def is_executable_content(path):
    if os.access(path, os.X_OK):
        return True
    try:
        with open(path, "rb") as f:
            head = f.read(2)
        return head == b"#!"
    except OSError:
        return False


def check_file(repo_root, rel_path, limit):
    full = os.path.join(repo_root, rel_path)
    v = []
    if os.path.islink(full):
        v.append("symlink-not-allowed")
    ext = os.path.splitext(rel_path)[1].lower()
    if ext not in ALLOWED_EXT:
        v.append("disallowed-type:" + ext)
    try:
        size = os.path.getsize(full)
        if size > limit:
            v.append("oversize:%d>%d" % (size, limit))
    except OSError:
        v.append("missing")
    if is_executable_content(full):
        v.append("executable-content")
    return v


def main(argv):
    changes_path = None
    repo = os.environ.get("REPO_ROOT", REPO_ROOT)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--changes":
            changes_path = argv[i + 1]; i += 2; continue
        elif a == "--repo":
            repo = argv[i + 1]; i += 2; continue
        elif a == "--self-test":
            return self_test()
        i += 1

    if changes_path is None:
        print("check-ai-writable: --changes <changes.json> required.", file=sys.stderr)
        return 42

    redlines = load_redlines()
    ai_paths = redlines.get("ai_writable_paths", []) or []
    limit = max_bytes(redlines)

    with open(changes_path, encoding="utf-8") as f:
        changes = json.load(f)

    violations = []
    for ch in changes:
        status = ch.get("status")
        if status in ("D",):
            continue
        path = ch.get("path") or ch.get("old_path")
        if path is None:
            continue
        if not under(path, ai_paths):
            continue  # 非 ai_writable 区域交给 R-SEC-001 / R-IMP-001
        vs = check_file(repo, path, limit)
        if vs:
            violations.append((path, vs))

    _last_violations.clear()
    _last_violations.extend(violations)

    if violations:
        for path, vs in violations:
            print("VIOLATION[R-SEC-004] %s: %s" % (path, ", ".join(vs)), file=sys.stderr)
        print("check-ai-writable: %d violation(s)." % len(violations), file=sys.stderr)
        return VIOLATION
    print("check-ai-writable: PASS (%d ai_writable files checked)." % len(changes))
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="caw-test-")
    try:
        redlines = {
            "thresholds": {"ai_writable_max_bytes": 1024},
            "ai_writable_paths": ["ai-reports/"],
        }
        with open(os.path.join(tmp, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(redlines, f, allow_unicode=True)
        aw = os.path.join(tmp, "ai-reports")
        os.makedirs(aw)
        ok = os.path.join(aw, "ok.md")
        with open(ok, "w", encoding="utf-8") as f:
            f.write("# ok draft\n")
        bad_type = os.path.join(aw, "evil.py")
        with open(bad_type, "w", encoding="utf-8") as f:
            f.write("print(1)\n")
        big = os.path.join(aw, "big.json")
        with open(big, "w", encoding="utf-8") as f:
            f.write("x" * 2000)
        link = os.path.join(aw, "link.md")
        os.symlink(ok, link)

        # 场景1：仅含合法文件 -> PASS
        ch1 = os.path.join(tmp, "c1.json")
        with open(ch1, "w", encoding="utf-8") as f:
            json.dump([{"path": "ai-reports/ok.md", "status": "A"}], f)
        os.environ["GOVERNANCE_DIR"] = tmp
        os.environ["REPO_ROOT"] = tmp
        rc1 = main(["--changes", ch1])
        assert rc1 == OK, "expected PASS for valid file, got %d" % rc1

        # 场景2：含非法文件 -> FAIL
        ch2 = os.path.join(tmp, "c2.json")
        with open(ch2, "w", encoding="utf-8") as f:
            json.dump([
                {"path": "ai-reports/ok.md", "status": "A"},
                {"path": "ai-reports/evil.py", "status": "A"},
                {"path": "ai-reports/big.json", "status": "M"},
                {"path": "ai-reports/link.md", "status": "A"},
            ], f)
        rc2 = main(["--changes", ch2])
        assert rc2 == VIOLATION, "expected VIOLATION, got %d" % rc2
        kinds = {k for _, vs in _last_violations for k in vs}
        assert "disallowed-type:.py" in kinds, kinds
        assert any(k.startswith("oversize:") for k in kinds), kinds
        assert "symlink-not-allowed" in kinds, kinds

        # 场景3：非 ai_writable 区域不在此校验（交给其它规则）
        ch3 = os.path.join(tmp, "c3.json")
        with open(ch3, "w", encoding="utf-8") as f:
            json.dump([{"path": "src/main.py", "status": "A"}], f)
        rc3 = main(["--changes", ch3])
        assert rc3 == OK, "files outside ai_writable should be skipped, got %d" % rc3

        print("SELF-TEST: check-ai-writable.py PASS (3 scenarios).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("GOVERNANCE_DIR", None)
        os.environ.pop("REPO_ROOT", None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
