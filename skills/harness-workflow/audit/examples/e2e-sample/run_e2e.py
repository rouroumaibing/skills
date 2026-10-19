#!/usr/bin/env python3
"""run_e2e.py —— 端到端样例：把 audit/scripts/ 的检查器按审计循环串起来跑一遍。

用法：
  python3 run_e2e.py                      # 两个场景都跑，逐项断言退出码（兼作集成测试）
  python3 run_e2e.py clean                # 只跑干净项目
  python3 run_e2e.py dirty --evidence out # 同时把每个检查器的原始输出写成证据附录 out/dirty.evidence.md

退出码：0 = 全部符合预期；1 = 有检查器的退出码与预期不符（检查器或样例回归了）。
注意：这里断言的是"检查器对样例的退出码"，即证据层。审计结论（哪些算缺口、哪些只是建议）
由审计者按 required_rules 采纳边界写进报告——见 expected/*.audit-report.md。
依赖：python3 + pyyaml。
"""
import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "scripts"          # audit/examples/e2e-sample -> audit/scripts
HEAD = "c0ffee1"                               # 被审提交（ci_gate_check --commit 的基准）

# (标签, 对应规则, 脚本, 参数, {场景: 预期退出码}, 说明)
CHECKS = [
    ("契约：禁用词 / 哈希 / 批准人", "R-GOAL-001 R-GOAL-002 R-REL-003", "check_contract.py",
     ["--contract", "contracts/C-001.md", "--check-words", "--check-hash", "--check-approver"],
     {"clean": 0, "dirty": 1}, "dirty：criteria 含「适当」；锁定后范围表被改，哈希对不上"),
    ("契约：是否已锁定", "R-GOAL-002", "check_contract.py",
     ["--contract", "contracts/C-001.md", "--check-locked", "--state", ".workflow/state.json"],
     {"clean": 0, "dirty": 1}, "dirty：state.json 仍是 draft"),
    ("改动范围 / 禁止项", "R-IMP-001 R-SEC-003", "pre-receive-scope",
     ["--changes", "audit-input/changes.json", "--contract", "contracts/C-001.md", "--check-forbidden"],
     {"clean": 0, "dirty": 1}, "dirty：改了范围外的 src/auth 与禁止项 config/prod.env"),
    ("风险下限", "R-RISK-001", "risk_floor_check.py",
     ["--changes", "audit-input/changes.json", "--diff", "audit-input/change.diff",
      "--contract-risk-level", "low"],
     {"clean": 0, "dirty": 1}, "dirty：改了 auth 目录并新增 DROP TABLE，下限为 high，契约却声明 low（E-17）"),
    ("测试完整性反模式", "R-IMP-004 R-IMP-003", "static_risk_check.py",
     ["--changes", "audit-input/changes.json", "--diff", "audit-input/change.diff"],
     {"clean": 0, "dirty": 1}, "dirty：新增 except Exception: pass 与 @pytest.mark.skip"),
    ("AI 可写区", "R-SEC-004", "check-ai-writable.py",
     ["--changes", "audit-input/changes.json", "--repo", "."],
     {"clean": 0, "dirty": 1}, "dirty：ai-reports/ 下新增了 .sh 可执行内容"),
    ("CI 门禁报告", "R-VER-006 R-VER-007 R-VER-001 R-VER-002", "ci_gate_check.py",
     ["--report", "audit-input/ci-report.json", "--commit", HEAD],
     {"clean": 0, "dirty": 1}, "dirty：报告属于旧提交（陈旧/重放）；覆盖率 71 < 80；变异分 55 < 60"),
    ("规则映射台账", "R-PRC-006", "rule_map_check.py", [],
     {"clean": 0, "dirty": 1}, "dirty：采纳了 R-VER-006 却无映射；台账里的 R-GOAL-002 未被采纳（替项目立法）"),
    ("文档一致性", "R-PRC-007", "doc_consistency_check.py", ["--root", "docs"],
     {"clean": 0, "dirty": 1}, "dirty：docs/README.md 有死链"),
    ("红绿顺序", "R-IMP-002", "tdd_order_check.py", [],
     {"clean": 42, "dirty": 42}, "两个项目都没接 CI 红绿证据：42 = 未接线，不是通过"),
    ("代码风格", "R-IMP-005", "lint", [],
     {"clean": 42, "dirty": 42}, "两个项目都没接 lint：42 = 未接线，不是通过"),
]


def stage(project: Path) -> Path:
    """把样例项目拷到临时目录再审：样例本身保持不变；*.in 文件在这里落成真实文件
    （例如 dirty 项目带死链的 docs/README.md——放在样例里会让本 skill 自己的文档自检失败）。"""
    tmp = Path(tempfile.mkdtemp(prefix="e2e-")) / project.name
    shutil.copytree(project, tmp, symlinks=True)
    for p in list(tmp.rglob("*.in")):
        p.rename(p.with_suffix(""))
    return tmp


def run_one(project: Path, script: str, args):
    env = dict(os.environ)
    env["GOVERNANCE_DIR"] = str(project / "governance")
    env["REPO_ROOT"] = str(project)
    env.pop("WORKFLOW_DIR", None)
    cmd = [sys.executable, str(SCRIPTS / script)] + args
    r = subprocess.run(cmd, cwd=str(project), env=env, capture_output=True, timeout=120)
    out = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
    return r.returncode, cmd, out


def headline(out: str) -> str:
    for key in ("VIOLATION", "NOT-WIRED", "PASS", "WARNING"):
        for ln in out.splitlines():
            if key in ln:
                return ln.strip()[:110]
    return (out.splitlines() or [""])[-1][:110]


def main(argv):
    scenarios = [a for a in argv if a in ("clean", "dirty")] or ["clean", "dirty"]
    evidence_dir = None
    if "--evidence" in argv:
        evidence_dir = Path(argv[argv.index("--evidence") + 1])
        evidence_dir.mkdir(parents=True, exist_ok=True)
    bad = 0
    for sc in scenarios:
        project = stage(HERE / ("%s-project" % sc))
        print("\n=== 场景：%s-project（被审提交 %s）===" % (sc, HEAD))
        print("%-26s %-5s %-5s %s" % ("检查器", "退出", "预期", "首要证据行"))
        ev = ["# 证据附录：%s-project\n" % sc,
              "> 由 `run_e2e.py %s` 生成：每个检查器的命令与原始输出，供审计报告逐条引用。\n" % sc]
        for label, rules, script, args, expect, note in CHECKS:
            rc, cmd, out = run_one(project, script, args)
            want = expect[sc]
            ok = (rc == want)
            bad += 0 if ok else 1
            print("%-26s %-5s %-5s %s%s" % (label, rc, want, headline(out), "" if ok else "   <-- 与预期不符"))
            ev.append("\n## %s（%s）\n" % (label, rules))
            ev.append("- 命令：`%s %s`（cwd = %s-project）" % (script, " ".join(args), sc))
            ev.append("- 退出码：**%s**（预期 %s）%s" % (rc, want, "" if ok else " ⚠ 不符"))
            if sc == "dirty":
                ev.append("- 样例设计：%s" % note)
            ev.append("\n```\n%s\n```" % out)
        if evidence_dir:
            (evidence_dir / ("%s.evidence.md" % sc)).write_text("\n".join(ev) + "\n", encoding="utf-8")
    print("\n%s" % ("全部符合预期。" if bad == 0 else "有 %d 项退出码与预期不符。" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
