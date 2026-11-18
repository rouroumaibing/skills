#!/usr/bin/env python3
"""coverage_table_check.py —— 建设期报告 §6 覆盖表的机器校验（元验证）。

审什么：审计报告「蓝图覆盖表」的**写作纪律是否被遵守**——这是
review-the-reviewers 的本地实例：覆盖表是报告自查的事实源，若它自己
自相矛盾或把并列子项压缩成一行，下游对照就失真（实测：两份报告的 §4.4
行都声称"四模式已在建议正文带出"，但正文均无"等价"，且同一行又挂
"详见蓝图（正文要点化）"免责——两个互斥断言并存）。

三条判据：

1. **禁自证断言**：去向栏不得出现"已在正文带出/列明/逐条列明"这类
   无法被读者核验的自证断言（实测：两份报告都写了这句而正文缺
   "等价"）。合法去向 = 建议编号（可多个）与/或 `详见蓝图 §4.x（正文
   要点化，细节不重复）`：编号指"建议在哪"，详见指"细节去哪看"，两者
   并存合法。**禁止的是"声称已带出"与"要点化"并存**——语义上既说全带了
   又说只写了要点，读者无法判断哪句为真。

2. **并列子项必须拆行**：§4.3（五子机制）/§4.4（四模式 + spec 漂移）/
   §4.7（双法）/§4.10（四子项）在覆盖表须逐项拆成独立行，不允许
   整行合并——整行压缩正是"等价/幂等"被吞掉的机制原因。

3. **处置三选一**：处置栏须以 `已建议` / `当前不适用` / `待建设` / `已覆盖`
   之一开头（VP 行可用"已覆盖"）；`当前不适用` 须有理由（去向栏非空且
   非纯破折号），`待建设` 须带 P0/P1/P2 阶段。

用法：
  python3 coverage_table_check.py --report <审计报告.md>
  python3 coverage_table_check.py --self-test

退出码：0 = PASS；1 = VIOLATION；42 = 未找到覆盖表 / 参数缺失（fail-closed，
不能因为报告没写覆盖表就当通过）。

注意：本检查器只做**文本形态**校验（断言互斥、拆行、三选一），不试图
用自然语言理解去核对"正文真有等价二字"——那是另一个带 bias 的判断者；
把"声称带出"这种不可核断言整体禁掉，比去验证它更可靠。
"""

import os
import re
import sys
import tempfile

OK = 0
VIOLATION = 1
NOT_WIRED = 42

# 并列子项条目 → 期望的最少拆行数（§4.4 spec 漂移另算一行，共 5）
MULTI_SECTIONS = {
    "4.3": (5, "五子机制"),
    "4.4": (5, "四模式 + spec 漂移"),
    "4.7": (2, "双法"),
    "4.10": (4, "四子项"),
}

# 不可核的自证断言（去向栏出现即违规）
SELF_CLAIM = re.compile(r"(已在.{0,12}正文.{0,6}(带出|列明|逐条列明)|"
                        r"四模式.{0,8}(列明|带出)|全.{0,4}在正文)")
# 合法形态 (b)：详见蓝图（正文要点化）
DEFER = re.compile(r"详见蓝图")
# 合法形态 (a)：指向建议编号（允许多个，如 建议 1/2/3）
POINTER = re.compile(r"建议\s*\d+")
DISPOSITIONS = ("已建议", "当前不适用", "待建设", "已覆盖")
PHASE = re.compile(r"P[012]")

_last = []


def _find_table(text):
    """定位覆盖表：从含'蓝图覆盖表'的标题到下一个二级标题，取其中的表格行。"""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^#{1,3}\s", ln) and "覆盖表" in ln:
            start = i + 1
            break
    if start is None:
        return []
    rows = []
    for ln in lines[start:]:
        if re.match(r"^#{1,3}\s", ln) and rows:
            break
        s = ln.strip()
        if s.startswith("|") and not re.match(r"^\|[\s\-:|]+\|$", s):
            cells = [c.strip() for c in s.strip("|").split("|")]
            # 跳过表头行（第 2 列字面上是"处置"）
            if len(cells) >= 3 and cells[1] != "处置":
                rows.append(cells)
    return rows


def check_text(text):
    """返回 findings 列表；不依赖文件，便于自测。"""
    findings = []
    rows = _find_table(text)
    if not rows:
        return ["coverage-table-missing"]

    counts = {}
    for idx, cells in enumerate(rows, 1):
        entry, disp, dest = cells[0], cells[1], cells[2]

        # 判据 3：处置三选一（归一化 markdown 加粗：**当前不适用**）
        disp_norm = disp.replace("*", "").strip()
        if not any(disp_norm.startswith(d) for d in DISPOSITIONS):
            findings.append("row%d-bad-disposition:%s" % (idx, disp[:20]))
        if disp_norm.startswith("当前不适用"):
            # 理由可写在处置栏（"**当前不适用**：无旧系统可对照"）或去向栏
            reason = disp_norm[len("当前不适用"):].strip("：: ") + " " + dest
            if not reason.strip("—- ") or len(reason.strip("—- ")) < 6:
                findings.append("row%d-not-applicable-without-reason" % idx)
        if disp.startswith("待建设") and not (PHASE.search(disp) or
                                               PHASE.search(dest)):
            findings.append("row%d-pending-without-phase" % idx)

        # 判据 1：禁自证断言 + 与免责声明互斥
        # 注意：「建议 N」与「详见蓝图」并存是合法的（编号指建议、详见指
        # 细节），实测教训：把它们判成互斥会误报大半张表。
        # 真正的矛盾是"声称已在正文带出"又"详见蓝图（正文要点化）"。
        has_claim = bool(SELF_CLAIM.search(dest))
        has_defer = bool(DEFER.search(dest))
        if has_claim:
            findings.append("row%d-unverifiable-self-claim:%s" %
                            (idx, dest[:40]))
        if has_claim and has_defer:
            findings.append("row%d-mutually-exclusive-destinations" % idx)

        # 判据 2：并列子项拆行（只统计蓝图条目栏明确写出节号的行）
        m = re.search(r"§4\.(\d+)", entry)
        if m:
            key = "4." + m.group(1)
            if key in MULTI_SECTIONS:
                counts[key] = counts.get(key, 0) + 1

    for key, (need, label) in MULTI_SECTIONS.items():
        got = counts.get(key, 0)
        if got and got < need:
            findings.append("section-%s-not-split:%d-of-%d (%s)" %
                            (key, got, need, label))
    return findings


def check(path):
    text = open(path, encoding="utf-8").read()
    f = check_text(text)
    _last.clear()
    _last.extend(f)
    return f


def main(argv):
    if "--self-test" in argv:
        return self_test()
    path = None
    for i, a in enumerate(argv):
        if a == "--report":
            path = argv[i + 1]
    if path is None:
        print("coverage_table_check: --report <report.md> required.",
              file=sys.stderr)
        return NOT_WIRED
    if not os.path.isfile(path):
        print("coverage_table_check: report not found: %s" % path,
              file=sys.stderr)
        return NOT_WIRED
    findings = check(path)
    if findings == ["coverage-table-missing"]:
        print("coverage_table_check: no coverage table found -> NOT-WIRED "
              "(建设期报告必须有 §6 覆盖表).", file=sys.stderr)
        return NOT_WIRED
    if findings:
        for f in findings:
            print("COVERAGE-ISSUE " + f, file=sys.stderr)
        print("coverage_table_check: %d finding(s) -> VIOLATION." %
              len(findings), file=sys.stderr)
        return VIOLATION
    print("coverage_table_check: PASS (destinations exclusive, sub-items "
          "split, dispositions valid).")
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="cov-tab-")

    header = ("# 审计报告\n\n## 七、蓝图覆盖表（§6 强制）\n\n"
              "> 处置三选一。\n\n"
              "| 蓝图条目 | 处置 | 去向（可核） |\n"
              "| --- | --- | --- |\n")

    def scenario(text):
        return check_text(text)

    # 场景1：合规——§4.4 拆成 5 行、去向只有编号或详见蓝图、处置合法
    good = header + "\n".join([
        "| §4.3 ① 规则从 base 分支读 | 已建议 | 建议 6（P1） |",
        "| §4.3 ② 评审绑定 commit | 已建议 | 建议 6（P1） |",
        "| §4.3 ③ code owner 写检查 | 已建议 | 建议 6（P1） |",
        "| §4.3 ④ 拉人须说 why | 已建议 | 建议 6（P1） |",
        "| §4.3 ⑤ 单一 reviewer 多轮 | 已建议 | 建议 6（P1） |",
        "| §4.4 ① round-trip | 已建议 | 建议 7（P2） |",
        "| §4.4 ② 不变量 | 已建议 | 建议 7（P2） |",
        "| §4.4 ③ 幂等 | 已建议 | 详见蓝图 §4.4（正文要点化，细节不重复） |",
        "| §4.4 ④ 等价 | 已建议 | 详见蓝图 §4.4（正文要点化，细节不重复） |",
        "| §4.4 spec 漂移时仍可靠 | 已建议 | 建议 7（P2） |",
        "| §4.7 第一法：三门槛 | 已建议 | 建议 8（P2） |",
        "| §4.7 第二法：bug finder 回放 | 已建议 | 建议 8（P2） |",
        "| §4.10 ① 基线管理 | 已建议 | 建议 3（P1） |",
        "| §4.10 ② 比对协议 | 已建议 | 建议 3（P1） |",
        "| §4.10 ③ 异常态驱动清单 | 已建议 | 建议 3（P1） |",
        "| §4.10 ④ 截图一等证据 | 已建议 | 建议 3（P1） |",
        "| §4.9 差分测试 | 当前不适用 | 无可并行旧实现，将来双跑再启用 |",
        "| §1 警告② 慢测试 | 待建设（P2） | 当前 smoke 轻量，后续重门放提交边界 |",
        "| VP-01 verify harness | 已覆盖 | §4.1 行 / 建议 2 |",
    ]) + "\n"
    assert not scenario(good), scenario(good)
    print("PASS scenario 1: compliant table")

    # 场景2：两份真实报告的偏差——§4.4 整行 + 双断言
    bad = header + "\n".join([
        "| §4.4 属性测试（round-trip / 不变量 / 幂等 / 等价四模式 + spec 漂移） | "
        "已建议 | 建议 7（P2，四模式与 spec 漂移已在建议正文带出）；"
        "详见蓝图 §4.4（正文要点化，细节不重复） |",
    ]) + "\n"
    f = scenario(bad)
    assert any("unverifiable-self-claim" in x for x in f), f
    assert any("mutually-exclusive-destinations" in x for x in f), f
    assert any("section-4.4-not-split" in x for x in f), f
    print("PASS scenario 2: real report defect caught (3 findings)")

    # 场景3：只有双断言但已拆行——仍违规（断言本身不可核）
    bad2 = header + "\n".join([
        "| §4.4 ① round-trip | 已建议 | 建议 7（P2，已在正文带出）；详见蓝图 §4.4（正文要点化） |",
        "| §4.4 ② 不变量 | 已建议 | 建议 7（P2） |",
        "| §4.4 ③ 幂等 | 已建议 | 建议 7（P2） |",
        "| §4.4 ④ 等价 | 已建议 | 建议 7（P2） |",
        "| §4.4 spec 漂移 | 已建议 | 建议 7（P2） |",
    ]) + "\n"
    f2 = scenario(bad2)
    assert any("unverifiable-self-claim" in x for x in f2), f2
    assert any("mutually-exclusive-destinations" in x for x in f2), f2
    assert not any("section-4.4-not-split" in x for x in f2), f2
    print("PASS scenario 3: self-claim caught even when split")

    # 场景4：处置三选一违规
    bad3 = header + ("| §4.2 验证戳 | 建议采纳 | 建议 4（P1） |\n"
                     "| §4.5 门禁灰度 | 当前不适用 | — |\n"
                     "| §4.6 flaky 治理 | 待建设 | 后续做 |\n")
    f3 = scenario(bad3)
    assert any("bad-disposition" in x for x in f3), f3
    assert any("not-applicable-without-reason" in x for x in f3), f3
    assert any("pending-without-phase" in x for x in f3), f3
    print("PASS scenario 4: disposition three-way enforced")

    # 场景5：无覆盖表 → 42（由 main 处理，这里只验 check_text）
    assert scenario("# 无覆盖表的报告\n正文。\n") == ["coverage-table-missing"]
    p = os.path.join(tmp, "no-cover.md")
    open(p, "w", encoding="utf-8").write("# 无覆盖表\n")
    assert main(["--report", p]) == NOT_WIRED
    assert main([]) == NOT_WIRED
    print("PASS scenario 5: missing table / missing arg -> 42 (fail-closed)")

    print("SELF-TEST: coverage_table_check.py PASS (5 scenarios).")
    return OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
