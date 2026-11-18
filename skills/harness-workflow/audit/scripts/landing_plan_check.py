#!/usr/bin/env python3
"""landing_plan_check.py —— 推荐落地计划的写作纪律机器校验（元验证）。

审什么：建设期"推荐落地计划"是否把报告的建议真正落到了施工批次里。这是
review-the-reviewers 的本地实例——计划是报告的下游，若它沉默漏带某条建议、
或每批写"验证通过"这种不可核的完成定义，使用者照着施工就会漏项或自欺。

判据（只做文本形态校验，不试图用自然语言理解推荐是否合理）：

1. **批次表存在**：含"批次总览"标题下、列头为 批次/目标/吸收建议/前置/
   验收 gate/需拍板 的 markdown 表（缺 → 42，fail-closed）。
2. **建议不漏带**：给了 --report 时，报告里 `### 建议 N` 的每个编号必须
   出现在某批的"吸收建议"栏或"当前不适用"表里，不能沉默漏掉。
3. **每批有可核验收 gate**：总览表"验收 gate"栏非空，且含可核信号
   （反引号命令 / 退出码 / rc= / 期望输出 / 文件路径）——"验证通过"不算。
4. **明细与总览一一对应**：每个总览批次在"各批次明细"有同名 `### 第N批：...`。
5. **需拍板项带推荐值**：需拍板表每行的"推荐值"与"默认动作"列非空。
6. **无版本叙述 / 自证断言**：正文不出现 v1/v2/版本沿革/完整重建/新增子机制
   等变更叙述，也不出现"已在正文带出/逐条列明"这类不可核自证。

用法：
  python3 landing_plan_check.py --plan <落地计划.md> [--report <审计报告.md>]
  python3 landing_plan_check.py --self-test

退出码：0 = PASS；1 = VIOLATION；42 = 无批次表 / 参数缺失 / 文件不存在。

语义部分（推荐顺序是否合理、语言使用者能否读懂）机检管不到，按
references/landing-plan.md 第六节人工核。
"""

import os
import re
import sys
import tempfile

OK = 0
VIOLATION = 1
NOT_WIRED = 42

HEADERS = ("批次", "目标", "吸收建议", "前置", "验收 gate", "需拍板")
GATE_SIGNAL = re.compile(r"`|退出码|rc\s*=|期望|输出|\.(go|md|yaml|yml|sh|mjs|json|py)\b")
VERSION_NARRATIVE = re.compile(
    r"v1 报告|本 v2|v2 为|版本沿革|完整重建|增量补丁|新增子机制|新增方法|改为原来的")
SELF_CLAIM = re.compile(r"已在.{0,8}正文.{0,4}(带出|列明)|逐条列明")


def _tables_after(text, heading_kw):
    """取含 heading_kw 的标题之后、下一个同级/更高级标题之前的表格行。"""
    lines = text.splitlines()
    start = None
    level = None
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,4})\s", ln)
        if m and heading_kw in ln:
            start, level = i + 1, len(m.group(1))
            break
    if start is None:
        return []
    rows = []
    for ln in lines[start:]:
        m = re.match(r"^(#{1,4})\s", ln)
        if m and len(m.group(1)) <= level and rows:
            break
        s = ln.strip()
        if s.startswith("|") and not re.match(r"^\|[\s\-:|]+\|$", s):
            cells = [c.strip() for c in s.strip("|").split("|")]
            rows.append(cells)
    return rows


def _col_index(header_cells, name):
    for i, c in enumerate(header_cells):
        if c.replace("*", "").strip() == name:
            return i
    return None


def _parse_batch_table(text):
    """返回 (header, data_rows)；找不到批次总览表返回 (None, [])。"""
    rows = _tables_after(text, "批次总览")
    if not rows:
        return None, []
    header = rows[0]
    if not all(_col_index(header, h) is not None for h in HEADERS):
        return None, []
    return header, rows[1:]


def _extract_report_suggestions(report_text):
    return set(int(n) for n in re.findall(r"^###\s*建议\s*(\d+)", report_text, re.M))


def _extract_plan_suggestions(text):
    """取"建议 1/2/3"形态的全部编号（模板允许斜杠 / 顿号分隔多个建议）。"""
    nums = set()
    for group in re.findall(r"建议\s*((?:\d+\s*[/、，,]\s*)*\d+)", text):
        for n in re.findall(r"\d+", group):
            nums.add(int(n))
    return nums


def check_text(text, report_text=None):
    findings = []
    header, rows = _parse_batch_table(text)
    if header is None:
        return ["batch-table-missing"]
    if not rows:
        findings.append("batch-table-empty")

    i_abs = _col_index(header, "吸收建议")
    i_gate = _col_index(header, "验收 gate")
    i_decide = _col_index(header, "需拍板")

    batch_names = []
    for idx, cells in enumerate(rows, 1):
        name = cells[0].replace("*", "").strip()
        batch_names.append(name)
        gate = cells[i_gate] if i_gate is not None and i_gate < len(cells) else ""
        if not gate or len(gate.strip()) < 4 or not GATE_SIGNAL.search(gate):
            findings.append("row%d-gate-not-verifiable:%s" % (idx, gate[:24]))
        decide = cells[i_decide] if i_decide is not None and i_decide < len(cells) else ""
        if decide and "见" in decide and not re.search(r"需拍板项", decide):
            findings.append("row%d-decision-unclear-link:%s" % (idx, decide[:24]))

    # 判据 4：明细与总览一一对应
    detail_heads = set(re.findall(r"^###\s+(第[一二三四五六七八九十\d]+批[：:].+)$",
                                  text, re.M))
    name_set = set(n.replace("*", "").strip() for n in batch_names)
    for name in name_set:
        if name not in detail_heads:
            findings.append("detail-missing-for-batch:%s" % name[:24])

    # 判据 2：报告建议不漏带（只在给了报告时校验）
    if report_text is not None:
        report_nums = _extract_report_suggestions(report_text)
        plan_nums = _extract_plan_suggestions(text)
        missing = sorted(report_nums - plan_nums)
        if missing:
            findings.append("suggestions-not-landed:" +
                            ",".join("建议%d" % n for n in missing))

    # 判据 5：需拍板项带推荐值与默认动作
    decision_rows = _tables_after(text, "需拍板项")
    if decision_rows:
        dh = decision_rows[0]
        ri = _col_index(dh, "推荐值")
        ai = _col_index(dh, "默认动作")
        if ri is None or ai is None:
            findings.append("decision-table-missing-columns")
        else:
            for idx, cells in enumerate(decision_rows[1:], 1):
                rec = cells[ri] if ri < len(cells) else ""
                act = cells[ai] if ai < len(cells) else ""
                if not rec.strip(" —-"):
                    findings.append("decision%d-no-recommendation" % idx)
                if not act.strip(" —-"):
                    findings.append("decision%d-no-default-action" % idx)

    # 判据 6：版本叙述 / 自证断言
    if VERSION_NARRATIVE.search(text):
        findings.append("version-narrative-present")
    if SELF_CLAIM.search(text):
        findings.append("unverifiable-self-claim")
    return findings


def check(plan_path, report_path=None):
    text = open(plan_path, encoding="utf-8").read()
    report_text = open(report_path, encoding="utf-8").read() if report_path else None
    f = check_text(text, report_text)
    return f


def main(argv):
    if "--self-test" in argv:
        return self_test()
    plan = report = None
    for i, a in enumerate(argv):
        if a == "--plan" and i + 1 < len(argv):
            plan = argv[i + 1]
        if a == "--report" and i + 1 < len(argv):
            report = argv[i + 1]
    if not plan:
        print("landing_plan_check: --plan <计划.md> required.", file=sys.stderr)
        return NOT_WIRED
    if not os.path.isfile(plan):
        print("landing_plan_check: plan not found: %s" % plan, file=sys.stderr)
        return NOT_WIRED
    if report and not os.path.isfile(report):
        print("landing_plan_check: report not found: %s" % report, file=sys.stderr)
        return NOT_WIRED
    findings = check(plan, report)
    if findings == ["batch-table-missing"]:
        print("landing_plan_check: no batch table -> NOT-WIRED "
              "(落地计划必须有批次总览表).", file=sys.stderr)
        return NOT_WIRED
    if findings:
        for f in findings:
            print("PLAN-ISSUE " + f, file=sys.stderr)
        print("landing_plan_check: %d finding(s) -> VIOLATION." % len(findings),
              file=sys.stderr)
        return VIOLATION
    print("landing_plan_check: PASS (batches complete, gates verifiable, "
          "decisions have recommendations).")
    return OK


def self_test():
    head = ("## 批次总览\n\n"
            "| 批次 | 目标 | 吸收建议 | 前置 | 验收 gate | 需拍板 |\n"
            "| --- | --- | --- | --- | --- | --- |\n")
    good_table = "\n".join([
        "| 第一批：修注释与接门 | 让 CI 说真话 | 建议 1/10 | 无 | `pnpm lint` rc=0，输出干净 | 无 |",
        "| 第二批：verify 模板 | 定义 done | 建议 4/5 | 第一批 | `verify done` 落退出码到 `evidence/` | 见需拍板项① |",
    ])
    detail = ("\n## 各批次明细\n\n### 第一批：修注释与接门\n\n- **验收 gate**：`pnpm lint` rc=0\n\n"
              "### 第二批：verify 模板\n\n- **验收 gate**：`verify done` 落退出码\n\n")
    decision = ("\n## 需拍板项（单列）\n\n"
                "| 编号 | 决策点 | 推荐值 | 理由 | 默认动作 |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| ① | 门何时转 enforce | 观察一周零误拦后 | 直接 enforce 会误拦 | 按推荐值执行 |\n")
    good = "# 落地计划\n\n" + head + good_table + detail + decision

    # 场景1：合规
    assert check_text(good) == [], check_text(good)
    print("PASS scenario 1: compliant landing plan")

    # 场景2：报告建议漏带（建议 7 没进计划）
    report = "### 建议 1（P0）\n### 建议 4（P1）\n### 建议 7（P2）\n"
    f2 = check_text(good, report)
    assert any("suggestions-not-landed" in x and "建议7" in x for x in f2), f2
    print("PASS scenario 2: dropped suggestion caught against report")

    # 场景3：验收 gate 不可核（"验证通过"）
    bad_gate = good.replace("`pnpm lint` rc=0，输出干净", "验证通过")
    f3 = check_text(bad_gate)
    assert any("row1-gate-not-verifiable" in x for x in f3), f3
    print("PASS scenario 3: unverifiable gate caught")

    # 场景4：明细缺失对应批次
    f4 = check_text(good.replace("### 第二批：verify 模板", "### 第二步：其他"))
    assert any("detail-missing-for-batch" in x for x in f4), f4
    print("PASS scenario 4: missing detail heading caught")

    # 场景5：需拍板项无推荐值
    f5 = check_text(good.replace("| 观察一周零误拦后 |", "|  |"))
    assert any("decision1-no-recommendation" in x for x in f5), f5
    print("PASS scenario 5: decision without recommendation caught")

    # 场景6：版本叙述 + 无批次表
    f6 = check_text(good + "\n本 v2 为完整重建。\n")
    assert any("version-narrative-present" in x for x in f6), f6
    assert check_text("# 无批次表\n正文") == ["batch-table-missing"]
    print("PASS scenario 6: version narrative + missing table")

    # 场景7：模板允许的"建议 1/10"分组写法不得被误判漏带
    grouped_report = ("### 建议 1（P0）\n### 建议 4（P1）\n"
                      "### 建议 5（P2）\n### 建议 10（P2）\n")
    f7 = check_text(good, grouped_report)
    assert not any("suggestions-not-landed" in x for x in f7), f7
    print("PASS scenario 7: slash-grouped suggestion refs not false-flagged")

    print("SELF-TEST: landing_plan_check.py PASS (7 scenarios).")
    return OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
