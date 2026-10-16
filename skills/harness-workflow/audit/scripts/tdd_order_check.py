#!/usr/bin/env python3
"""tdd_order_check.py —— R-IMP-002 实装（依赖红绿证据存储）。

服务规则 R-IMP-002：每条自动类判据在实现提交之前已存在对应的失败测试，且曾被运行失败
（以测试历史/红绿记录判定，非提交顺序；squash/rebase 不影响）。

纯静态脚本无法可靠判定"测试曾失败"——必须依赖 CI 提供的红绿历史。
因此本脚本读取一个"红绿证据日志"（JSONL，由 CI 从测试运行记录生成），
校验每条判据对应的测试，其"首次失败"出现在"实现提交"之前。
无证据日志时退出 42（未实装；不得静默放行）。

证据日志格式（每行一个 JSON）：{"test_id":"T-1","commit":"abc123","result":"pass"|"fail"}
按时间（commit 顺序）排列。另需 --impl-commits <json list> 给出实现提交顺序，
--criterion-tests <json> 映射 判据ID->[test_id,...]。

用法：
  tdd_order_check.py --redgreen-log <log.jsonl> --impl-commits <commits.json> --criterion-tests <map.json> [--self-test]
  tdd_order_check.py --self-test
"""
import os
import sys
import json
import tempfile
import shutil

VIOLATION = 1
NOT_WIRED = 42
OK = 0
_last = []


def earliest(commits_in_order, test_results):
    """返回 (首次失败位置, 首次出现位置)。位置 = 在 commits_in_order 的索引。"""
    fail_pos = None
    first_pos = None
    for idx, c in enumerate(commits_in_order):
        for rec in test_results.get(c, []):
            if first_pos is None:
                first_pos = idx
            if rec == "fail" and fail_pos is None:
                fail_pos = idx
    return fail_pos, first_pos


def main(argv):
    rg = impl = cmap = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--redgreen-log":
            rg = argv[i + 1]; i += 2; continue
        elif a == "--impl-commits":
            impl = argv[i + 1]; i += 2; continue
        elif a == "--criterion-tests":
            cmap = argv[i + 1]; i += 2; continue
        elif a == "--self-test":
            return self_test()
        i += 1
    if rg is None or impl is None or cmap is None:
        print("tdd_order_check: requires --redgreen-log/--impl-commits/--criterion-tests "
              "(red-green evidence store produced by CI). Exit 42 = not implemented.", file=sys.stderr)
        return NOT_WIRED

    commits = json.load(open(impl, encoding="utf-8"))
    c2t = json.load(open(cmap, encoding="utf-8"))
    # 按 commit 顺序归并测试结果
    results = {}
    with open(rg, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            rec = json.loads(ln)
            results.setdefault(rec["commit"], {}).setdefault(rec["test_id"], rec["result"])

    violations = []
    impl_commits = [c for c in commits if c.endswith("_impl")]
    earliest_impl = next((i for i, c in enumerate(commits) if c in impl_commits), None)
    for crit, tests in c2t.items():
        for tid in tests:
            fail_pos = next((i for i, c in enumerate(commits)
                            if results.get(c, {}).get(tid) == "fail"), None)
            if fail_pos is None:
                violations.append("no-failing-test:%s/%s" % (crit, tid))
                continue
            if earliest_impl is not None and fail_pos >= earliest_impl:
                violations.append("impl-before-test:%s/%s" % (crit, tid))

    _last.clear()
    _last.extend(violations)
    if violations:
        for m in violations:
            print("VIOLATION[R-IMP-002] " + m, file=sys.stderr)
        print("tdd_order_check: %d violation(s)." % len(violations), file=sys.stderr)
        return VIOLATION
    print("tdd_order_check: PASS (red-green evidence satisfied TDD order).")
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="tdd-test-")
    try:
        # commit 顺序：commit1(测试失败) commit2(实现_impl) commit3(测试通过)
        commits = ["c1", "c2_impl", "c3"]
        json.dump(commits, open(os.path.join(tmp, "impl.json"), "w"))
        cmap = {"A-1": ["T-1"]}
        json.dump(cmap, open(os.path.join(tmp, "cmap.json"), "w"))
        log = os.path.join(tmp, "rg.jsonl")
        with open(log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"test_id": "T-1", "commit": "c1", "result": "fail"}) + "\n")
            f.write(json.dumps({"test_id": "T-1", "commit": "c3", "result": "pass"}) + "\n")

        # 场景1：测试先于实现失败 -> PASS
        rc1 = main(["--redgreen-log", log, "--impl-commits", os.path.join(tmp, "impl.json"),
                    "--criterion-tests", os.path.join(tmp, "cmap.json")])
        assert rc1 == OK, "expected PASS, got %d / %s" % (rc1, _last)

        # 场景2：无失败记录 -> FAIL
        log2 = os.path.join(tmp, "rg2.jsonl")
        with open(log2, "w", encoding="utf-8") as f:
            f.write(json.dumps({"test_id": "T-1", "commit": "c3", "result": "pass"}) + "\n")
        rc2 = main(["--redgreen-log", log2, "--impl-commits", os.path.join(tmp, "impl.json"),
                    "--criterion-tests", os.path.join(tmp, "cmap.json")])
        assert rc2 == VIOLATION and any("no-failing-test" in m for m in _last), _last

        # 场景3：实现先于测试失败 -> FAIL（impl 在 c0，失败记录在 c1）
        commits3 = ["c0_impl", "c1"]
        json.dump(commits3, open(os.path.join(tmp, "impl3.json"), "w"))
        log3 = os.path.join(tmp, "rg3.jsonl")
        with open(log3, "w", encoding="utf-8") as f:
            f.write(json.dumps({"test_id": "T-1", "commit": "c1", "result": "fail"}) + "\n")
        rc3 = main(["--redgreen-log", log3, "--impl-commits", os.path.join(tmp, "impl3.json"),
                    "--criterion-tests", os.path.join(tmp, "cmap.json")])
        assert rc3 == VIOLATION and any("impl-before-test" in m for m in _last), _last

        # 场景4：无证据日志（缺参数）-> 42
        rc4 = main(["--impl-commits", os.path.join(tmp, "impl.json"),
                    "--criterion-tests", os.path.join(tmp, "cmap.json")])
        assert rc4 == NOT_WIRED

        print("SELF-TEST: tdd_order_check.py PASS (4 scenarios).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
