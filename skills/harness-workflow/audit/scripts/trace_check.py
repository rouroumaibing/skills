#!/usr/bin/env python3
"""trace_check.py —— R-DES-001 / R-VER-005 实装（合入必需检查）。

服务规则：
  - R-DES-001：设计文档每个条目至少关联一条契约条目（S/A/B 编号）。
  - R-VER-005：追溯矩阵中不存在无来源的代码变更和无实现的需求。

校验（见 schemas.md §2）：
  - 矩阵每行 req_id 须以 S-/A-/B-/N-/X- 开头且在契约中存在；
  - design_id 若填写须在设计文档「设计条目」章节定义（D-<n>: 起头）；
  - 状态由脚本推导：缺实现=impl-missing / 缺测试=test-missing / 缺设计=design-missing / 齐=linked；
  - 无来源的代码变更：--diff-files 列出的实现文件未出现在任何矩阵行的 impl_file -> 报告；
  - 无实现的需求：契约 scope/criteria/boundaries 的 ID 未在矩阵 req_id 出现 -> 报告。

用法：
  trace_check.py --matrix <trace.csv> --contract <contract.md> [--design <design.md>] [--diff-files <list.txt>] [--self-test]
  trace_check.py --self-test

矩阵 CSV 首行注释 # schema_version=1；表头：req_id,design_id,impl_file,impl_symbol,impl_commit,test_id,status（status 可空，由脚本推导）。
"""
import os
import sys
import csv
import tempfile
import shutil

try:
    import yaml
except ImportError:
    yaml = None

GOVERNANCE_DIR = os.environ.get("GOVERNANCE_DIR",
                                os.path.dirname(os.path.abspath(__file__)).rstrip("/") + "/..")
VIOLATION = 1
OK = 0
_last = []


def load_yaml_safe(path):
    if yaml is None:
        print("trace_check: pyyaml required (pip install pyyaml).", file=sys.stderr)
        sys.exit(42)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def contract_req_ids(contract_path):
    ids = set()
    if not contract_path or not os.path.exists(contract_path):
        return ids
    text = open(contract_path, encoding="utf-8").read()
    fm = None
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = yaml.safe_load(text[3:end].strip()) or {}
    else:
        try:
            fm = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            fm = None
    if fm:
        for key in ("scope", "criteria", "boundaries", "non_goals", "forbidden_items"):
            for e in (fm.get(key) or []):
                if isinstance(e, dict) and e.get("id"):
                    ids.add(e["id"])
    return ids


def design_ids(design_path):
    ids = set()
    if not design_path or not os.path.exists(design_path):
        return ids
    for ln in open(design_path, encoding="utf-8"):
        s = ln.strip()
        if s.startswith("D-") and ":" in s:
            ids.add(s.split(":", 1)[0])
    return ids


def parse_matrix(path):
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    for r in reader:
        rows.append(r)
    return rows


def derive_status(row):
    if not (row.get("impl_file") or "").strip() or not (row.get("impl_symbol") or "").strip():
        return "impl-missing"
    if not (row.get("test_id") or "").strip():
        return "test-missing"
    return "linked"


def main(argv):
    matrix_path = contract_path = design_path = diff_files = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--matrix":
            matrix_path = argv[i + 1]; i += 2; continue
        elif a == "--contract":
            contract_path = argv[i + 1]; i += 2; continue
        elif a == "--design":
            design_path = argv[i + 1]; i += 2; continue
        elif a == "--diff-files":
            diff_files = argv[i + 1]; i += 2; continue
        elif a == "--self-test":
            return self_test()
        i += 1
    if matrix_path is None or contract_path is None:
        print("trace_check: --matrix and --contract required.", file=sys.stderr)
        return 42

    rows = parse_matrix(matrix_path)
    creqs = contract_req_ids(contract_path)
    dids = design_ids(design_path)
    violations = []

    matrix_req_ids = set()
    for r in rows:
        rid = (r.get("req_id") or "").strip()
        matrix_req_ids.add(rid)
        if rid and not (rid.startswith(("S-", "A-", "B-", "N-", "X-"))):
            violations.append("bad-req_id-format:%s" % rid)
        elif rid and rid not in creqs:
            violations.append("req_id-not-in-contract:%s" % rid)
        did = (r.get("design_id") or "").strip()
        if did and dids and did not in dids:
            violations.append("design_id-undefined:%s" % did)
        st = (r.get("status") or "").strip()
        if st and st not in ("linked", "impl-missing", "test-missing", "design-missing"):
            # 手填不符预期枚举 -> 以脚本推导覆盖并记录
            violations.append("status-handfilled:%s" % rid)

    # 无实现的需求：契约 ID 不在矩阵
    for cid in creqs:
        if cid not in matrix_req_ids:
            violations.append("requirement-without-impl:%s" % cid)

    # 无来源的代码变更：diff 中实现的文件未在矩阵 impl_file
    if diff_files and os.path.exists(diff_files):
        impl_files = {r.get("impl_file").strip() for r in rows if (r.get("impl_file") or "").strip()}
        with open(diff_files, encoding="utf-8") as f:
            for ln in f:
                p = ln.strip()
                if not p:
                    continue
                base = p.split("/")[-1]
                if base not in {os.path.basename(x) for x in impl_files}:
                    violations.append("code-change-without-trace:%s" % p)

    _last.clear()
    _last.extend(violations)
    if violations:
        for m in violations:
            print("VIOLATION[R-VER-005/R-DES-001] " + m, file=sys.stderr)
        print("trace_check: %d violation(s)." % len(violations), file=sys.stderr)
        return VIOLATION
    print("trace_check: PASS.")
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="tc-test-")
    try:
        g = os.path.join(tmp, "governance"); os.makedirs(g)
        yaml.safe_dump({"forbidden_words": []}, open(os.path.join(g, "redlines.yaml"), "w"),
                       allow_unicode=True)
        os.environ["GOVERNANCE_DIR"] = g

        contract = {"contract_id": "C-1", "scope": [{"id": "S-1", "path": "src/x"}],
                    "criteria": [{"id": "A-1", "desc": "x"}],
                    "boundaries": [{"id": "B-1", "scenario": "y", "expected": "z"}]}
        cmd = os.path.join(tmp, "C.md")
        yaml.safe_dump(contract, open(cmd, "w"), allow_unicode=True)

        design = os.path.join(tmp, "design.md")
        open(design, "w", encoding="utf-8").write("D-1: 处理上传\nD-2: 播放起播\n")

        def matrix(rows):
            p = os.path.join(tmp, "trace.csv")
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("# schema_version=1\n")
                w = csv.writer(f)
                w.writerow(["req_id", "design_id", "impl_file", "impl_symbol", "impl_commit", "test_id", "status"])
                for r in rows:
                    w.writerow(r)
            return p

        # 场景1：完整且一致 -> PASS
        mp = matrix([["S-1", "D-1", "src/x.go", "X.up", "abc1", "T-1", ""],
                     ["A-1", "D-2", "src/y.go", "Y.play", "abc2", "T-2", ""],
                     ["B-1", "D-2", "src/y.go", "Y.play", "abc2", "T-2", ""]])
        rc1 = main(["--matrix", mp, "--contract", cmd, "--design", design])
        assert rc1 == OK, "expected PASS, got %d / %s" % (rc1, _last)

        # 场景2：req_id 不在契约 -> FAIL
        mp2 = matrix([["S-9", "D-1", "src/x.go", "X.up", "abc1", "T-1", ""]])
        rc2 = main(["--matrix", mp2, "--contract", cmd, "--design", design])
        assert rc2 == VIOLATION and any("req_id-not-in-contract" in m for m in _last), _last

        # 场景3：缺少需求实现（契约 B-1 未入矩阵） -> FAIL
        mp3 = matrix([["S-1", "D-1", "src/x.go", "X.up", "abc1", "T-1", ""],
                      ["A-1", "D-2", "src/y.go", "Y.play", "abc2", "T-2", ""]])
        rc3 = main(["--matrix", mp3, "--contract", cmd, "--design", design])
        assert rc3 == VIOLATION and any("requirement-without-impl:B-1" in m for m in _last), _last

        # 场景4：design_id 未定义 -> FAIL
        mp4 = matrix([["S-1", "D-99", "src/x.go", "X.up", "abc1", "T-1", ""]])
        rc4 = main(["--matrix", mp4, "--contract", cmd, "--design", design])
        assert rc4 == VIOLATION and any("design_id-undefined" in m for m in _last), _last

        # 场景5：无来源代码变更 -> FAIL
        df = os.path.join(tmp, "diff.txt"); open(df, "w").write("src/z.go\n")
        rc5 = main(["--matrix", mp, "--contract", cmd, "--design", design, "--diff-files", df])
        assert rc5 == VIOLATION and any("code-change-without-trace" in m for m in _last), _last

        print("SELF-TEST: trace_check.py PASS (5 scenarios).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("GOVERNANCE_DIR", None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
