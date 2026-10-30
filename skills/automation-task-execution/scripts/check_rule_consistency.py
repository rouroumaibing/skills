#!/usr/bin/env python3
"""Check that the rules stated in several documents stay in sync.

The same handful of rules is written down in more than one place: the
skill overview, the detailed triage reference, and the copy-paste prompts.
That is deliberate — the prompts have to stand on their own so they still
work when pasted into a chat — but it means a change made in one file can
silently go missing in the others. The result is two documents that
disagree about, say, how many retries are allowed.

This script checks the parameters that must not drift: the retry limit,
the four authorization-boundary categories, and the assertion rules. It
also flags superseded wordings (FORBIDDEN) whose presence would silently
reintroduce a fixed rule conflict. It
only *reports* — it never edits the documents.

Usage:
    python check_rule_consistency.py [skill-dir] [--json]

The skill directory defaults to the parent of this script's directory.

Exit code is 0 when every rule is consistent, 1 when at least one drifts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# The documents that state the rules, in the order they are reported.
DOCS: Tuple[str, ...] = (
    "SKILL.md",
    "references/failure-triage.md",
    "references/prompt-templates.md",
)

# Documents that are allowed not to state a given rule. The prompt
# templates are self-contained by design, so they state every rule; the
# triage reference is the detailed home of the failure-handling rules.
OPTIONAL: Dict[str, Tuple[str, ...]] = {}


@dataclass
class Rule:
    """One rule that must read the same everywhere it appears.

    `pattern` extracts the parameter from a document; the values found in
    different documents have to agree. A rule without a capture group is
    compared by the matched text itself, so two different wordings of the
    same rule count as a drift — the point is to keep one wording.
    """
    name: str
    pattern: str                 # regex, optionally with one capture group
    docs: Tuple[str, ...] = DOCS  # documents that must state it
    note: str = ""


# The rules that carry a parameter — a number or a fixed list. These are
# the ones where a drift actually changes what the agent is allowed to do.
RULES: List[Rule] = [
    Rule(
        name="重试上限",
        pattern=r"重试\s*(\d+)\s*次",
        note="超过上限后必须停下汇报，而不是继续重试",
    ),
    Rule(
        name="授权边界类别",
        pattern=r"(共享库\s*/\s*生产库|生产库\s*/\s*共享库)",
        note="四类越界操作的首类；其余三类按同一清单核对",
    ),
    Rule(
        name="禁止放宽断言",
        pattern=r"(不得为(?:让用例通过|通过用例)而?放宽断言)",
        note="放宽断言必须先经人工确认；措辞也要统一，否则读者会当成两条规则",
    ),
    Rule(
        name="B类信息禁止假设",
        pattern=r"不允许假设，只能问人",
        docs=("SKILL.md", "references/failure-triage.md"),
        note="凭证、授权范围、期望值来源、完成定义只能由人提供；"
             "prompt-templates 的提问提示词里不出现该句属预期",
    ),
    Rule(
        name="B类阻塞处理",
        pattern=r"阻塞的 B 类，解决前不得动手；非阻塞的 B 类，可先做不依赖它的部分，同时等回复",
        docs=("SKILL.md", "references/prompt-templates.md", "templates/gap-analysis.md"),
        note="B 类统一处理口径，判定依据是每题的「阻塞：是/否」，不用另加字段",
    ),
    Rule(
        name="D类默认假设口径",
        pattern=r"不算待确认项，不阻塞",
        docs=("SKILL.md", "references/prompt-templates.md", "templates/gap-analysis.md",
              "templates/execution-plan.md"),
        note="D 类默认假设写入「已采用的默认假设」，交付时汇总；不得混入待确认项，"
             "否则会误触 Phase 2「待确认项非空必须停下」的停止条件",
    ),
    Rule(
        name="四对账清单",
        pattern=r"断言[、+\s]*代码模式[、+\s]*资源池姿态[、+\s]*TODO\s*清零",
        docs=("SKILL.md", "templates/execution-plan.md", "references/prompt-templates.md"),
        note="完成判据对账扩为四项：断言、代码模式、资源池姿态、TODO 清零；"
             "枚举顺序与措辞三处必须一致，漏一项就回到只看退出码的老路",
    ),
    Rule(
        name="骨架示意不得照抄",
        pattern=r"方案示意[，、\s]*不得直接照抄",
        docs=("SKILL.md", "templates/execution-plan.md", "references/prompt-templates.md"),
        note="骨架代码只能当方案示意，实现以现有代码的真实写法为准；"
             "照抄骨架等于把推断当事实",
    ),
    Rule(
        name="缺口分析独立成文件",
        pattern=r"独立成文件[^。]*不得合并",
        docs=("SKILL.md", "templates/gap-analysis.md"),
        note="gap-analysis.md 必须独立成文件，不得合并进其它设计文档；"
             "缺口分析有自己的生命周期（回填台账、事后确认）",
    ),
]

# Wordings that must not appear anywhere. These are superseded phrasings
# that would silently reintroduce a fixed conflict — unlike the rules
# above, the check flags the *presence* of the text, not disagreement.
# Matching is exact by design: a rewording of a superseded phrase slips
# through, so append the new wording here when it reappears in testing.
FORBIDDEN: List[Tuple[str, str]] = [
    ("AI 不确定或做了假设",
     "旧措辞会把「做了假设」的地方塞回待确认项，触发「待确认项非空必须停」。"
     "正确口径：待确认项只收「AI 无法判断的地方」，默认假设放 §4b"),
]


@dataclass
class Finding:
    rule: str
    severity: str      # 高 / 中
    detail: str
    evidence: List[str]


def read_doc(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def collect_values(text: str, rule: Rule) -> List[str]:
    """All values the rule takes in one document.

    A rule without a capture group is compared by the matched wording, so
    two different phrasings inside one document count as a drift — the
    check exists to keep one wording, not to accept synonyms.
    """
    out: List[str] = []
    for m in re.finditer(rule.pattern, text):
        raw = m.group(1) if m.groups() else m.group(0)
        value = " ".join(raw.split())
        if value not in out:
            out.append(value)
    return out


def check(skill_dir: str) -> List[Finding]:
    findings: List[Finding] = []

    # Collect every document any rule references — a rule may be pinned to
    # a template file that is not part of the default DOCS set.
    all_docs: List[str] = []
    for d in DOCS:
        if d not in all_docs:
            all_docs.append(d)
    for rule in RULES:
        for d in rule.docs:
            if d not in all_docs:
                all_docs.append(d)
    texts = {d: read_doc(os.path.join(skill_dir, d)) for d in all_docs}

    # Superseded wordings must not survive anywhere in the rule documents.
    for d, text in texts.items():
        if not text:
            continue
        for phrase, why in FORBIDDEN:
            if phrase in text:
                findings.append(Finding(
                    rule="禁用语",
                    severity="高",
                    detail=f"`{d}` 出现已被取代的措辞「{phrase}」：{why}",
                    evidence=[],
                ))

    missing_docs = [d for d, t in texts.items() if not t]
    for d in missing_docs:
        findings.append(Finding(
            rule="文档存在性",
            severity="高",
            detail=f"规则文档缺失，无法核对一致性：{d}",
            evidence=[],
        ))

    for rule in RULES:
        per_doc = {d: collect_values(texts[d], rule) for d in rule.docs}

        # A document that does not state the rule at all.
        for d in rule.docs:
            if d in missing_docs:
                continue
            if not per_doc[d]:
                findings.append(Finding(
                    rule=rule.name,
                    severity="中",
                    detail=f"`{d}` 未声明「{rule.name}」，与其它文档可能不一致"
                          + (f"（{rule.note}）" if rule.note else ""),
                    evidence=[],
                ))

        # The same document stating the rule with different values — a
        # self-contradiction, which is easy to introduce and hard to spot.
        for d, vals in per_doc.items():
            if len(vals) > 1:
                findings.append(Finding(
                    rule=rule.name,
                    severity="高",
                    detail=f"`{d}` 内部对「{rule.name}」给出了不同取值："
                           + "、".join(repr(v) for v in vals),
                    evidence=[],
                ))

        # The same rule stated with different values across documents.
        distinct = {}
        for d, vals in per_doc.items():
            for v in vals:
                distinct.setdefault(v, []).append(d)
        if len(distinct) > 1:
            detail = f"「{rule.name}」在不同文档中取值不一致：" + "；".join(
                f"{' / '.join(v)} → {k!r}" for k, v in sorted(distinct.items()))
            findings.append(Finding(
                rule=rule.name,
                severity="高",
                detail=detail,
                evidence=[f"{d}: {per_doc[d]}" for d in rule.docs if per_doc[d]],
            ))

    return findings


def main(argv: Optional[List[str]] = None) -> int:
    default_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("skill_dir", nargs="?", default=default_dir,
                        help="skill directory holding the rule documents")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    findings = check(args.skill_dir)

    if args.json:
        print(json.dumps([{
            "rule": f.rule, "severity": f.severity,
            "detail": f.detail, "evidence": f.evidence,
        } for f in findings], ensure_ascii=False, indent=2))
        return 1 if findings else 0

    if not findings:
        print(f"✓ {len(RULES)} 条规则在 {len(DOCS)} 份文档中一致。")
        return 0

    order = {"高": 0, "中": 1}
    findings.sort(key=lambda f: (order.get(f.severity, 2), f.rule))
    print(f"发现 {len(findings)} 处规则不一致（需人工核对，脚本不自动修改）：\n")
    for f in findings:
        print(f"[{f.severity}] {f.rule}")
        print(f"    {f.detail}")
        for e in f.evidence:
            print(f"    - {e}")
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
