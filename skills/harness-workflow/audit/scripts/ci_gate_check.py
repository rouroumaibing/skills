#!/usr/bin/env python3
"""ci_gate_check.py —— R-VER-006/007 实装（合入必需 CI 门禁）+ R-VER-001/002 阈值检查。

审什么：CI 产出的**门禁报告**（JSON），检查
  1. 必需 step 全部 pass（默认必需：type-check、dep-secret-scan，对应
     R-VER-006 / R-VER-007；可用 redlines.yaml 的 `ci_required_steps` 覆盖/追加）；
  2. metrics 阈值（对应 R-VER-001 覆盖率 / R-VER-002 变异测试得分）：
     redlines.yaml `thresholds` 中声明了 `coverage` / `mutation_score` 时，
     报告 `metrics` 中的同名值不得低于阈值，且须落在 [0, 100] 合理区间；
  3. 提交绑定（防陈旧报告重放）：调用方传入 `--commit <sha>` 时，报告的
     `commit` 字段必须存在、非空且与之相等——缺失/为空 → 42（报告不完整），
     不相等 → 1（陈旧或重放的报告，按违反处理）。未传 `--commit` 时打警告
     （证据未绑定提交，报告须注明）。

报告格式（由 CI 写出，本项目不产出报告）：
  {"commit": "<sha>", "steps": {"type-check": "pass", "dep-secret-scan": "pass"},
   "metrics": {"coverage": 82.5, "mutation_score": 71.0}}
step 取值：pass | fail（其他值视为非法）。

判据来源（fail-closed）：
  - 报告缺失 / 非法 JSON（含 NaN / Infinity 字面量）/ 非法结构 → 42（未接线，
    不得当通过）；
  - 必需 step 未出现在报告中 → 42（该门禁未运行，不得当通过）；
  - thresholds 声明了阈值但报告缺对应 metric → 42（度量未跑，不得当通过）；
  - metric 数值越界（< 0 或 > 100）→ 42（不可能的度量值，报告可疑）；
  - 任一必需 step = fail 或任一 metric 低于阈值 → 1（违反）。

退出码：0 通过 / 1 违反 / 42 未接线。

阈值判据的采纳语义：`thresholds` 未声明 coverage/mutation_score 时，本检查器
只核对必需 step 并在 PASS 行**明示"0 metric threshold(s)"**——覆盖率/变异
判据未声明 = 项目未采纳 R-VER-001/002 的量化红线，属建议模式范畴，但必须
显式可见，不得让读者误以为度量已核对。

用法：
  ci_gate_check.py [--report <ci-report.json>] [--governance-dir <dir>]
      [--commit <sha>] [--self-test]
  --report 缺省读 <GOVERNANCE_DIR>/.workflow/ci-report.json（如存在）；
  --governance-dir 缺省取环境变量 GOVERNANCE_DIR，再缺省 governance/。
"""
import os
import sys
import io
import json
import re
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
DEFAULT_REQUIRED_STEPS = ("type-check", "dep-secret-scan")
KNOWN_METRICS = ("coverage", "mutation_score")

_last = []


def _load_yaml(path):
    if yaml is None:
        print("ci_gate_check: pyyaml required (pip install pyyaml).", file=sys.stderr)
        sys.exit(42)
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        print("ci_gate_check: %s is corrupt (not valid YAML); treat as NOT-WIRED.\n  %s"
              % (path, e), file=sys.stderr)
        sys.exit(42)


def _argv_value(argv, flag):
    for i, a in enumerate(argv):
        if a == flag:
            return argv[i + 1]
    return None


def _load_report(path):
    """读报告 JSON；NaN/Infinity 是 JSON 规范外的字面量，Python 默认会放行——
    这里显式拒绝（NaN < 80 恒为 False，等于让门禁静默通过）。"""
    def _reject(const):
        raise ValueError("non-finite JSON literal %r is not a valid measurement" % const)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f, parse_constant=_reject)
    except (ValueError, OSError) as e:
        print("ci_gate_check: CI gate report is corrupt or contains NaN/Infinity; "
              "treat as NOT-WIRED.\n  %s" % e, file=sys.stderr)
        return None


def main(argv):
    if "--self-test" in argv:
        return self_test()
    gov_dir = _argv_value(argv, "--governance-dir") \
        or os.environ.get("GOVERNANCE_DIR", GOVERNANCE_DIR)
    redlines = {}
    rl_path = os.path.join(gov_dir, "redlines.yaml")
    if os.path.exists(rl_path):
        redlines = _load_yaml(rl_path)
    if not isinstance(redlines, dict):
        print("ci_gate_check: redlines.yaml is not a mapping; treat as NOT-WIRED.",
              file=sys.stderr)
        return NOT_WIRED

    required = list(DEFAULT_REQUIRED_STEPS)
    extra = redlines.get("ci_required_steps")
    if extra is not None:
        if not isinstance(extra, list) or not all(isinstance(x, str) and x.strip() for x in extra):
            print("ci_gate_check: 'ci_required_steps' must be a list of non-empty "
                  "strings; treat as NOT-WIRED.", file=sys.stderr)
            return NOT_WIRED
        required = sorted(set(required) | set(extra))

    thresholds = redlines.get("thresholds") or {}
    if not isinstance(thresholds, dict):
        print("ci_gate_check: 'thresholds' must be a mapping; treat as NOT-WIRED.",
              file=sys.stderr)
        return NOT_WIRED
    metric_thresholds = {k: v for k, v in thresholds.items()
                         if k in KNOWN_METRICS and v is not None}
    for k, v in metric_thresholds.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            print("ci_gate_check: threshold %r must be a number (got %r); treat as "
                  "NOT-WIRED." % (k, v), file=sys.stderr)
            return NOT_WIRED
        if not (0 <= v <= 100):
            print("ci_gate_check: threshold %r out of plausible range [0, 100] "
                  "(got %r); treat as NOT-WIRED." % (k, v), file=sys.stderr)
            return NOT_WIRED

    report_path = _argv_value(argv, "--report") \
        or os.path.join(gov_dir, ".workflow", "ci-report.json")
    if not os.path.exists(report_path):
        print("ci_gate_check: CI gate report not found: %s — required gates NOT run; "
              "treat as NOT-WIRED (fail-closed, wire CI before enabling as required "
              "check). To bootstrap: have CI write the report JSON described in the "
              "module docstring." % report_path, file=sys.stderr)
        return NOT_WIRED
    report = _load_report(report_path)
    if report is None:
        return NOT_WIRED
    if not isinstance(report, dict) or not isinstance(report.get("steps"), dict):
        print("ci_gate_check: CI gate report must be an object with a 'steps' "
              "mapping; treat as NOT-WIRED.", file=sys.stderr)
        return NOT_WIRED

    want_commit = _argv_value(argv, "--commit")
    rep_commit = report.get("commit")
    violations, not_wired = [], []
    if want_commit is not None:
        if not isinstance(rep_commit, str) or not rep_commit.strip():
            not_wired.append("report 'commit' missing/empty while --commit was "
                             "given (report not bound to a commit)")
        elif rep_commit != want_commit:
            violations.append("report 'commit' = %r != expected %r (stale or "
                              "replayed report)" % (rep_commit, want_commit))
    else:
        print("WARNING: report commit not bound (pass --commit <sha> to check for "
              "stale/replayed reports).", file=sys.stderr)

    steps = report["steps"]
    for s in required:
        state = steps.get(s)
        if state is None:
            not_wired.append("required step %r not in report (gate not run)" % s)
        elif state not in ("pass", "fail"):
            not_wired.append("step %r has illegal state %r (pass|fail)" % (s, state))
        elif state == "fail":
            violations.append("required step %r = fail" % s)

    metrics = report.get("metrics") or {}
    if not isinstance(metrics, dict):
        not_wired.append("'metrics' must be a mapping when present")
    for k, th in metric_thresholds.items():
        val = metrics.get(k)
        if val is None:
            not_wired.append("threshold %r declared but metric missing from report "
                             "(measurement not run)" % k)
        elif not isinstance(val, (int, float)) or isinstance(val, bool):
            not_wired.append("metric %r must be a number (got %r)" % (k, val))
        elif not (0 <= val <= 100):
            not_wired.append("metric %r = %s outside plausible range [0, 100] "
                             "(implausible measurement, report suspect)" % (k, val))
        elif val < th:
            violations.append("metric %r = %s < threshold %s" % (k, val, th))

    _last.clear()
    _last.extend(violations)
    if not_wired:
        for m in not_wired:
            print("NOT-WIRED " + m, file=sys.stderr)
        return NOT_WIRED
    if violations:
        for m in violations:
            print("VIOLATION " + m, file=sys.stderr)
        print("ci_gate_check: %d violation(s) -> VIOLATION "
              "(R-VER-001/002/006/007)." % len(violations), file=sys.stderr)
        return VIOLATION
    msg = ("ci_gate_check: PASS (%d required step(s), %d metric threshold(s), "
           "commit=%s)." % (len(required), len(metric_thresholds),
                            rep_commit if isinstance(rep_commit, str) and rep_commit
                            else "unbound"))
    if not metric_thresholds:
        msg += (" NOTE: no coverage/mutation thresholds declared — R-VER-001/002 "
                "rulers not adopted; steps checked only.")
    print(msg)
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="ci-gate-")
    old_gov = os.environ.get("GOVERNANCE_DIR")
    try:
        def gov(name, redlines=None, report=None):
            d = os.path.join(tmp, name)
            os.makedirs(os.path.join(d, ".workflow"), exist_ok=True)
            if redlines is not None:
                with open(os.path.join(d, "redlines.yaml"), "w", encoding="utf-8") as f:
                    yaml.safe_dump(redlines, f)
            if report is not None:
                with open(os.path.join(d, ".workflow", "ci-report.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(report, f)
            return d

        def run(d, *extra_args):
            os.environ.pop("GOVERNANCE_DIR", None)
            args = ["--governance-dir", d,
                    "--report", os.path.join(d, ".workflow", "ci-report.json")]
            args += list(extra_args)
            try:
                return main(args)
            except SystemExit as e:
                return e.code

        good = {"commit": "abc", "steps": {"type-check": "pass", "dep-secret-scan": "pass"},
                "metrics": {}}

        # 场景1：报告缺失 -> 42
        rc1 = run(gov("g1"))
        assert rc1 == NOT_WIRED, "expected 42 (no report), got %d" % rc1

        # 场景2：全部 pass -> 0
        rc2 = run(gov("g2", report=good))
        assert rc2 == OK, "expected PASS, got %d" % rc2

        # 场景3：type-check fail -> 1
        r3 = dict(good); r3["steps"] = {"type-check": "fail", "dep-secret-scan": "pass"}
        rc3 = run(gov("g3", report=r3))
        assert rc3 == VIOLATION, "expected VIOLATION (type-check fail), got %d" % rc3

        # 场景4：必需 step 缺失 -> 42
        r4 = dict(good); r4["steps"] = {"type-check": "pass"}
        rc4 = run(gov("g4", report=r4))
        assert rc4 == NOT_WIRED, "expected 42 (dep-secret-scan not run), got %d" % rc4

        # 场景5：非法 step 状态 -> 42
        r5 = dict(good); r5["steps"] = {"type-check": "pass", "dep-secret-scan": "ok?"}
        rc5 = run(gov("g5", report=r5))
        assert rc5 == NOT_WIRED, "expected 42 (illegal state), got %d" % rc5

        # 场景6：报告损坏 -> 42
        d6 = os.path.join(tmp, "g6"); os.makedirs(os.path.join(d6, ".workflow"))
        with open(os.path.join(d6, ".workflow", "ci-report.json"), "w") as f:
            f.write("{oops")
        rc6 = run(d6)
        assert rc6 == NOT_WIRED, "expected 42 (corrupt report), got %d" % rc6

        # 场景7：声明 coverage 阈值、报告缺 metric -> 42
        rc7 = run(gov("g7", redlines={"thresholds": {"coverage": 80}}, report=good))
        assert rc7 == NOT_WIRED, "expected 42 (coverage metric missing), got %d" % rc7

        # 场景8：coverage 82.5 >= 80 -> 0
        r8 = dict(good); r8["metrics"] = {"coverage": 82.5}
        rc8 = run(gov("g8", redlines={"thresholds": {"coverage": 80}}, report=r8))
        assert rc8 == OK, "expected PASS (coverage above threshold), got %d" % rc8

        # 场景9：mutation_score 60 < 65 -> 1
        r9 = dict(good); r9["metrics"] = {"mutation_score": 60}
        rc9 = run(gov("g9", redlines={"thresholds": {"mutation_score": 65}}, report=r9))
        assert rc9 == VIOLATION, "expected VIOLATION (mutation below threshold), got %d" % rc9

        # 场景10：额外必需 step（ci_required_steps）未跑 -> 42
        rc10 = run(gov("g10", redlines={"ci_required_steps": ["e2e-smoke"]}, report=good))
        assert rc10 == NOT_WIRED, "expected 42 (extra required step not run), got %d" % rc10

        # 场景11：额外必需 step 通过 -> 0
        r11 = dict(good); r11["steps"] = dict(good["steps"], **{"e2e-smoke": "pass"})
        rc11 = run(gov("g11", redlines={"ci_required_steps": ["e2e-smoke"]}, report=r11))
        assert rc11 == OK, "expected PASS (extra step pass), got %d" % rc11

        # 场景12：ci_required_steps 非列表 -> 42
        rc12 = run(gov("g12", redlines={"ci_required_steps": "oops"}, report=good))
        assert rc12 == NOT_WIRED, "expected 42 (bad ci_required_steps), got %d" % rc12

        # 场景13：thresholds 非数值 -> 42
        rc13 = run(gov("g13", redlines={"thresholds": {"coverage": "high"}}, report=good))
        assert rc13 == NOT_WIRED, "expected 42 (non-numeric threshold), got %d" % rc13

        # ---- v10 加固场景 ----
        # 场景14：coverage 为 NaN -> 42（v10 前 NaN < 80 为假 -> PASS）
        d14 = os.path.join(tmp, "g14")
        os.makedirs(os.path.join(d14, ".workflow"), exist_ok=True)
        with open(os.path.join(d14, ".workflow", "ci-report.json"), "w") as f:
            f.write('{"commit":"a","steps":{"type-check":"pass","dep-secret-scan":"pass"},'
                    '"metrics":{"coverage": NaN}}')
        with open(os.path.join(d14, "redlines.yaml"), "w") as f:
            f.write("thresholds: {coverage: 80}\n")
        rc14 = run(d14)
        assert rc14 == NOT_WIRED, "expected 42 (NaN coverage), got %d" % rc14

        # 场景15：--governance-dir 参数生效（v10 前被忽略）：
        # 无环境变量、只传参数，thresholds 里声明了 coverage 但 metric 缺失 -> 42
        rc15 = run(gov("g15", redlines={"thresholds": {"coverage": 80}}, report=good))
        assert rc15 == NOT_WIRED, \
            "expected 42 (--governance-dir must be honored), got %d" % rc15

        # 场景16：--commit 绑定，报告 commit 不匹配 -> 1（陈旧/重放报告）
        rc16 = run(gov("g16", report=dict(good, commit="OLD")), "--commit", "HEADSHA")
        assert rc16 == VIOLATION, "expected VIOLATION (stale report), got %d" % rc16

        # 场景17：--commit 绑定，报告缺 commit -> 42
        r17 = dict(good); r17.pop("commit")
        rc17 = run(gov("g17", report=r17), "--commit", "HEADSHA")
        assert rc17 == NOT_WIRED, "expected 42 (report w/o commit), got %d" % rc17

        # 场景18：--commit 绑定且匹配 -> 0
        rc18 = run(gov("g18", report=dict(good, commit="HEADSHA")), "--commit", "HEADSHA")
        assert rc18 == OK, "expected PASS (commit bound & matched), got %d" % rc18

        # 场景19：metric 越界（coverage 1000 / -5）-> 42
        r19 = dict(good); r19["metrics"] = {"coverage": 1000}
        rc19 = run(gov("g19", redlines={"thresholds": {"coverage": 80}}, report=r19))
        assert rc19 == NOT_WIRED, "expected 42 (implausible metric), got %d" % rc19
        r19b = dict(good); r19b["metrics"] = {"coverage": -5}
        rc19b = run(gov("g19b", redlines={"thresholds": {"coverage": 80}}, report=r19b))
        assert rc19b == NOT_WIRED, "expected 42 (negative metric), got %d" % rc19b

        # 场景20：阈值越界（coverage 180）-> 42
        rc20 = run(gov("g20", redlines={"thresholds": {"coverage": 180}}, report=good))
        assert rc20 == NOT_WIRED, "expected 42 (implausible threshold), got %d" % rc20

        # 场景21：metrics 里也是 NaN -> 42
        d21 = os.path.join(tmp, "g21")
        os.makedirs(os.path.join(d21, ".workflow"), exist_ok=True)
        with open(os.path.join(d21, ".workflow", "ci-report.json"), "w") as f:
            f.write('{"commit":"a","steps":{"type-check":"pass","dep-secret-scan":"pass"},'
                    '"metrics":{"mutation_score": Infinity}}')
        rc21 = run(d21)
        assert rc21 == NOT_WIRED, "expected 42 (Infinity metric), got %d" % rc21

        # 场景22：commit 为 bool/数字等非法类型 + --commit -> 42
        rc22 = run(gov("g22", report=dict(good, commit=123)), "--commit", "123")
        assert rc22 == NOT_WIRED, "expected 42 (non-string commit), got %d" % rc22

        print("SELF-TEST: ci_gate_check.py PASS (22 scenarios).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if old_gov is None:
            os.environ.pop("GOVERNANCE_DIR", None)
        else:
            os.environ["GOVERNANCE_DIR"] = old_gov


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
