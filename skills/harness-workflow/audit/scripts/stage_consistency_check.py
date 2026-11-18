#!/usr/bin/env python3
"""stage_consistency_check.py —— 建设期报告「阶段标注与证据来源」一致性机检。

蓝图 §3 的阶段归属权威表 + §6 的阶段标注纪律的机器判据。
coverage_table_check.py 管"蓝图每项去了哪"，本器管"报告的阶段说法是否
自相矛盾"——覆盖表机检 PASS 不等于阶段说法可信（实测：某报告机检 PASS，
却一边说"P0 基本成型"一边把 D7/D10 判 ❌，把 D4 标 P0、D10 塞进 P2）。

三条判据（文本形态判定，不做语义理解）：

1. 建议标题阶段 vs【缺口】行所有 D 维度权威阶段
   标题 `### 建议 N（PX）...` 取阶段；该建议【缺口】行**所有** D 编号
   （D4b 归一 D4）逐一查权威表，任一维度阶段与标题不一致即违规——防止把
   P0 的 D7 悄悄埋进 P1 建议（实测盲区：旧版只取第一个 D，D8/D7 里 D8=P1
   匹配就放行，漏掉夹带的 D7=P0）。合法出口：正文显式写
   "主阶段=PX，附带 Y 属 PZ" 且声明行含所有相关 D 的权威阶段。
2. 摘要阶段判定 vs D 表
   摘要出现 "PX 成型/达标/就绪/完成"，而 D 表中该阶段任一维度为 ❌ → 违规。
   ⛔ 当前不适用视为不拖累阶段；⚠️ 部分视为未完全达标（保守）。
3. 证据来源与判定信号同检查器
   D11 结论涉及 flaky 关键词（sleep/rerun/skip/flaky/重跑/重试）却全文无
   `static_risk_check` → 违规；D8 涉及验证戳关键词却全文无
   `verification_gate_check` → 违规。防止拿相邻粗扫的 "0 命中" 冒名顶替。

用法：
  python3 stage_consistency_check.py --report <审计报告.md>
  python3 stage_consistency_check.py --self-test

退出码：0 PASS / 1 VIOLATION / 42 未接线（无 --report、无 D 表、无建议标题）。

盲区（报告须注明"机器推算仅供参考"）：只核【缺口】行的 D 维度，
若报告把跨阶段维度写在正文别处而非【缺口】行，本器看不到；跨阶段声明的
语义正确性（是否真的拆项）仍须人核。本器只保证"说法自洽"，不保证"建议
内容真的落地"。
"""

import argparse
import re
import sys

# 阶段归属权威表（单一事实源：references/verification-program.md §3）。
# 改蓝图阶段表时须同步此处，并在 README §19 登记。
D_STAGE = {
    "D1": "P0", "D7": "P0", "D10": "P0",
    "D4": "P1", "D8": "P1", "D9": "P1",
    "D3": "P2", "D5": "P2", "D11": "P2", "D12": "P2",
}

D_ROW = re.compile(r"^\|\s*(D\d+b?)[^|]*\|")
VERDICT = re.compile(r"(✅|⚠️|❌|⛔)")
SUG_TITLE = re.compile(r"^#{2,4}\s*建议\s*(\d+)\s*[（(]\s*(P[012])\b")
GAP_DS = re.compile(r"D\d+b?")  # 【缺口】行内取所有 D（不再只取第一个）
DECLARED = re.compile(r"主阶段\s*=\s*(P[012])")
SUMMARY_STAGE = re.compile(r"(P[012])\s*(?:基本|初步)?\s*(成型|达标|就绪|完成)")
FLAKY_TERMS = re.compile(r"sleep|rerun|skip|flaky|重跑|重试", re.I)
STAMP_TERMS = re.compile(r"戳|stamp|Verified", re.I)


def norm_d(d):
    return "D4" if d == "D4b" else d


def strip_fences(text):
    out, inside = [], False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            inside = not inside
            continue
        if not inside:
            out.append(ln)
    return "\n".join(out)


def parse_d_table(text):
    rows = {}
    for ln in text.splitlines():
        m = D_ROW.match(ln.strip())
        if not m:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        v = VERDICT.search(cells[1])
        if v:
            rows[norm_d(m.group(1))] = v.group(1)
    return rows


def parse_suggestions(text):
    lines = text.splitlines()
    sugs = []
    for i, ln in enumerate(lines):
        m = SUG_TITLE.match(ln.strip())
        if not m:
            continue
        body = []
        for l2 in lines[i + 1:]:
            if SUG_TITLE.match(l2.strip()) or re.match(r"^##\s", l2.strip()):
                break
            body.append(l2)
        sugs.append((int(m.group(1)), m.group(2), "\n".join(body)))
    return sugs


def check_text(text):
    findings = []
    text = strip_fences(text)
    d_rows = parse_d_table(text)
    sugs = parse_suggestions(text)
    if not d_rows:
        return ["no-d-table"]
    if not sugs:
        return ["no-suggestions"]

    # 判据 1：建议标题阶段 vs【缺口】行所有 D 维度的权威阶段
    for num, title_stage, body in sugs:
        gap_line = (body.split("【缺口】", 1)[-1].split("\n")[0]
                    if "【缺口】" in body else "")
        related_ds = [norm_d(x) for x in GAP_DS.findall(gap_line)]
        if not related_ds:
            continue
        # 任一 D 的权威阶段与标题不一致即为跨阶段夹带（不再只看第一个）
        off = [(d, D_STAGE[d]) for d in related_ds
               if d in D_STAGE and D_STAGE[d] != title_stage]
        if not off:
            continue
        # 合法出口：显式声明跨阶段，且声明行覆盖所有相关 D 的权威阶段
        decl_lines = [ln for ln in body.splitlines() if DECLARED.search(ln)]
        stages_in_decl = " ".join(decl_lines)
        covered = all(
            D_STAGE.get(d, title_stage) in stages_in_decl
            for d in related_ds
        )
        if not (decl_lines and covered):
            findings.append(
                "suggestion-%d-stage-mismatch:off=%s:title=%s"
                % (num, ",".join("%s(%s)" % (d, st) for d, st in off), title_stage)
            )

    # 判据 2：摘要阶段判定 vs D 表
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("|") or s.startswith("#"):
            continue
        for m in SUMMARY_STAGE.finditer(s):
            stage, _ = m.group(1), m.group(2)
            dims = [d for d, st in D_STAGE.items() if st == stage]
            failed = [d for d in dims if d_rows.get(d) == "❌"]
            if failed:
                findings.append(
                    "summary-stage-claim:%s:failed=%s" % (stage, ",".join(failed))
                )

    # 判据 3：证据来源与判定信号同检查器
    d11_ev = ""
    d8_ev = ""
    for ln in text.splitlines():
        if ln.strip().startswith("| D11 "):
            d11_ev = ln
        if ln.strip().startswith("| D8 "):
            d8_ev = ln
    if d11_ev and FLAKY_TERMS.search(d11_ev) and "static_risk_check" not in text:
        findings.append("evidence-missing-checker:D11:static_risk_check")
    if d8_ev and STAMP_TERMS.search(d8_ev) and "verification_gate_check" not in text:
        findings.append("evidence-missing-checker:D8:verification_gate_check")

    return findings


def self_test():
    base = """# 审计报告

当前处于 P0 成型、P1 成型、P2 成型 的阶段。

## 二、证据总表（D1~D12 逐维核对）

| 维度 | 判定 | 证据 |
| --- | --- | --- |
| D1 静态门 | ✅ 达标 | CI 有 typecheck |
| D7 证据协议 | ✅ 达标 | 由 harness 收集 |
| D10 升级策略 | ✅ 达标 | 配了阈值 |
| D4 E2E 冒烟 | ✅ 达标 | survey verify_asset 有 |
| D8 验证戳 | ✅ 达标 | verification_gate_check.py 实跑 |
| D9 判卷独立性 | ✅ 达标 | REVIEW 清单 |
| D3 属性测试 | ✅ 达标 | fast-check |
| D5 差分测试 | ⛔ 当前不适用 | 无旧实现 |
| D11 flaky 治理 | ✅ 达标 | static_risk_check.py 实跑，无 sleep/rerun |
| D12 漏检回流 | ✅ 达标 | 有规则 |

## 四、修改建议

### 建议 1（P0）静态门
【缺口】D1 不完整。

### 建议 2（P1）verify 资产
【缺口】D4 缺失。

### 建议 3（P2）属性测试
【缺口】D3 缺失。

### 建议 4（P0）升级策略
【缺口】D10 缺失。
"""
    cases = [
        ("ok", base, 0),
        ("suggestion-p0-on-p1", base.replace("建议 2（P1）", "建议 2（P0）"), 1),
        ("suggestion-p2-on-p0", base.replace("建议 4（P0）", "建议 4（P2）"), 1),
        ("hidden-cross-stage",
         base.replace("【缺口】D4 缺失。", "【缺口】D4/D7 缺失。"), 1),
        ("summary-lie", base.replace("| D7 证据协议 | ✅ 达标 |", "| D7 证据协议 | ❌ 缺失 |"), 1),
        ("evidence-d11", base.replace("| D11 flaky 治理 | ✅ 达标 | static_risk_check.py 实跑，无 sleep/rerun |",
                                      "| D11 flaky 治理 | ✅ 达标 | S5 粗扫 skip 命中 0，无 sleep/rerun |"), 1),
        ("declared-cross-stage",
         base.replace("【缺口】D10 缺失。", "【缺口】D10/D12 缺失；主阶段=P2，附带 D10 属 P0，已拆项。"), 0),
        ("no-d-table", "# 报告\n### 建议 1（P0）\n【缺口】D1 不完整。\n", 42),
        ("no-suggestions", "# 报告\n| D1 静态门 | ✅ 达标 |\n", 42),
    ]
    failed = 0
    for name, text, want in cases:
        f = check_text(text)
        rc = 42 if f and f[0].startswith("no-") else (1 if f else 0)
        ok = rc == want
        print(("PASS " if ok else "FAIL ") + name + " rc=" + str(rc) + " findings=" + repr(f))
        if not ok:
            failed += 1
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", help="审计报告 markdown 路径")
    ap.add_argument("--self-test", action="store_true", help="跑内嵌反例夹具")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.report:
        print("stage_consistency_check: --report required (or --self-test)", file=sys.stderr)
        return 42
    try:
        text = open(args.report, encoding="utf-8").read()
    except (OSError, UnicodeError) as e:
        print("stage_consistency_check: cannot read report: %s" % e, file=sys.stderr)
        return 42
    findings = check_text(text)
    if not findings:
        print("stage_consistency_check: PASS (stages and evidence sources consistent)")
        return 0
    for f in findings:
        print("STAGE-ISSUE " + f)
    return 1


if __name__ == "__main__":
    sys.exit(main())
