#!/usr/bin/env python3
"""Validate the structured entries of an automation context file.

A context file that the agent appends to after every real failure is the main
defence against re-discovering the same pitfall. It also rots: ports change,
endpoints are retired, a temporary workaround quietly becomes the long-term
design. Structured entries plus a periodic re-validation are what keep it
useful.

This script checks the entries against the required shape and flags entries
that have not been re-validated for too long. It only *reports* — it never
edits the file.

Usage:
    python check_context_entries.py <context-file> [--max-age-days 30] [--json]

Entry shape (one heading per entry, then `- field: value` lines):

    ### E-001 <一句话现象>

    - 类别：环境
    - 现象：<实际报错原文 / 表现>
    - 归因：<根因>
    - 解法：<怎么修的>
    - 类型：根因修复 | 临时 workaround
    - 适用范围：<服务 / 模块 / 用例范围>
    - 记录日期：YYYY-MM-DD
    - 复核状态：待复核 | 已复核
    - 最近复核：YYYY-MM-DD      (required once 复核状态 is 已复核)

Exit code is 0 when no problem is found, 1 when at least one problem is found.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

REQUIRED_FIELDS = ("类别", "现象", "归因", "解法", "类型", "适用范围", "记录日期", "复核状态")

TYPE_ROOT_FIX = "根因修复"
TYPE_WORKAROUND = "临时 workaround"

_TYPE_ALIASES = {
    "根因修复": TYPE_ROOT_FIX,
    "根治": TYPE_ROOT_FIX,
    "根因": TYPE_ROOT_FIX,
    "修复": TYPE_ROOT_FIX,
    "root fix": TYPE_ROOT_FIX,
    "rootfix": TYPE_ROOT_FIX,
    "临时 workaround": TYPE_WORKAROUND,
    "临时workaround": TYPE_WORKAROUND,
    "临时方案": TYPE_WORKAROUND,
    "workaround": TYPE_WORKAROUND,
    "临时": TYPE_WORKAROUND,
}

_STATUS_PENDING = "待复核"
_STATUS_DONE = "已复核"

_FIELD_RE = re.compile(r"^\s*[-*]\s*(?P<key>[^:：]{1,20})\s*[:：]\s*(?P<val>.*)$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*$")
_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


@dataclass
class Entry:
    heading: str
    line_no: int
    fields: Dict[str, str] = field(default_factory=dict)
    field_lines: Dict[str, int] = field(default_factory=dict)


@dataclass
class Finding:
    entry: str
    line: Optional[int]
    kind: str
    severity: str
    detail: str


def parse_entries(path: str) -> List[Entry]:
    """Parse a context file into entries.

    Fields belong to the nearest preceding heading of any level, and a heading
    is an entry only when entry-shaped fields follow it directly. That keeps
    plain section headings (which merely introduce entries) from being
    mistaken for entries themselves.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    entries: List[Entry] = []
    current: Optional[Entry] = None
    in_code_fence = False

    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            current = None
            continue
        if in_code_fence:
            continue

        heading = _HEADING_RE.match(raw)
        if heading:
            current = Entry(heading=heading.group("text"), line_no=idx)
            entries.append(current)
            continue

        m = _FIELD_RE.match(raw)
        if m and current is not None:
            key = m.group("key").strip()
            val = m.group("val").strip()
            current.fields.setdefault(key, val)
            current.field_lines.setdefault(key, idx)
    return entries


def is_entry(entry: Entry) -> bool:
    """An entry carries at least one of the canonical field names."""
    return any(k in entry.fields for k in REQUIRED_FIELDS)


def parse_date(text: str) -> Optional[_dt.date]:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normalize_type(text: str) -> Optional[str]:
    key = (text or "").strip().lower()
    if not key:
        return None
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    for alias, canonical in _TYPE_ALIASES.items():
        if alias in key:
            return canonical
    return None


def check_entry(entry: Entry, today: _dt.date, max_age: int) -> List[Finding]:
    findings: List[Finding] = []
    name = entry.heading

    for key in REQUIRED_FIELDS:
        if key not in entry.fields:
            findings.append(Finding(
                entry=name, line=entry.line_no, kind="字段缺失", severity="高",
                detail=f"缺少必填字段「{key}」"))
        elif not entry.fields[key].strip():
            findings.append(Finding(
                entry=name, line=entry.field_lines.get(key), kind="字段为空", severity="高",
                detail=f"字段「{key}」为空"))

    raw_type = entry.fields.get("类型", "")
    norm_type = normalize_type(raw_type)
    if raw_type.strip() and norm_type is None:
        findings.append(Finding(
            entry=name, line=entry.field_lines.get("类型"), kind="类型不合规", severity="高",
            detail=f"类型「{raw_type}」不是「{TYPE_ROOT_FIX}」或「{TYPE_WORKAROUND}」"))

    status = entry.fields.get("复核状态", "").strip()
    if status and status not in (_STATUS_PENDING, _STATUS_DONE):
        findings.append(Finding(
            entry=name, line=entry.field_lines.get("复核状态"), kind="复核状态不合规", severity="中",
            detail=f"复核状态「{status}」不是「{_STATUS_PENDING}」或「{_STATUS_DONE}」"))

    recorded = parse_date(entry.fields.get("记录日期", ""))
    if entry.fields.get("记录日期", "").strip() and recorded is None:
        findings.append(Finding(
            entry=name, line=entry.field_lines.get("记录日期"), kind="日期不可解析", severity="中",
            detail=f"记录日期「{entry.fields.get('记录日期')}」不是可识别的日期"))

    reviewed_raw = entry.fields.get("最近复核", "")
    reviewed = parse_date(reviewed_raw) if reviewed_raw.strip() else None
    if status == _STATUS_DONE and reviewed is None:
        findings.append(Finding(
            entry=name, line=entry.field_lines.get("复核状态"), kind="字段缺失", severity="高",
            detail="复核状态为「已复核」但缺少「最近复核」日期"))

    # staleness: how long since the last known-good check
    basis = reviewed or recorded
    if basis is not None:
        age = (today - basis).days
        if age > max_age:
            if status == _STATUS_DONE:
                findings.append(Finding(
                    entry=name, line=entry.field_lines.get("最近复核"),
                    kind="复核记录过期", severity="中",
                    detail=f"距上次复核已 {age} 天（阈值 {max_age} 天），需重新用当前代码 / 命令确认是否仍成立"))
            else:
                findings.append(Finding(
                    entry=name, line=entry.field_lines.get("记录日期"),
                    kind="过期未复核", severity="中",
                    detail=f"记录于 {age} 天前且从未复核（阈值 {max_age} 天）；"
                           f"workaround 类条目腐化更快，优先复核"))
    return findings


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", help="context file to validate")
    parser.add_argument("--max-age-days", type=int, default=30,
                        help="entries older than this (and not re-validated) are flagged (default 30)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.file):
        print(f"文件不存在：{args.file}", file=sys.stderr)
        return 2

    entries = parse_entries(args.file)
    real_entries = [e for e in entries if is_entry(e)]
    today = _dt.date.today()

    findings: List[Finding] = []
    for entry in real_entries:
        findings.extend(check_entry(entry, today, args.max_age_days))

    # Entries that look like entries but were written without their own heading
    # end up merged into a section heading; call that out explicitly.
    for entry in entries:
        if entry in real_entries:
            continue
        present = [k for k in REQUIRED_FIELDS if k in entry.fields]
        if len(present) >= 3:
            findings.append(Finding(
                entry=entry.heading, line=entry.line_no, kind="条目缺少独立标题", severity="中",
                detail=f"该段落含 {len(present)} 个条目字段但没有自己的标题行，"
                       f"多个条目可能被合并成一条；每个条目请单独起一个标题"))

    counts = {"总条目": len(real_entries)}
    for entry in real_entries:
        t = normalize_type(entry.fields.get("类型", ""))
        key = "根因修复" if t == TYPE_ROOT_FIX else ("临时 workaround" if t == TYPE_WORKAROUND else "未标注类型")
        counts[key] = counts.get(key, 0) + 1

    if args.json:
        print(json.dumps({
            "file": args.file,
            "max_age_days": args.max_age_days,
            "counts": counts,
            "findings": [{"entry": f.entry, "line": f.line, "kind": f.kind,
                          "severity": f.severity, "detail": f.detail} for f in findings],
        }, ensure_ascii=False, indent=2))
        return 1 if findings else 0

    if not real_entries:
        print(f"⚠ 未在 {args.file} 中找到结构化条目。")
        print("  条目格式：每个条目一个标题行，随后是 `- 字段：值` 的字段行"
              "（类别 / 现象 / 归因 / 解法 / 类型 / 适用范围 / 记录日期 / 复核状态）。")
        return 1

    print(f"条目统计：{' | '.join(f'{k} {v}' for k, v in counts.items())}")
    if not findings:
        print("✓ 所有条目字段完整、类型合规，且都在有效期内。")
        return 0

    order = {"高": 0, "中": 1, "低": 2}
    findings.sort(key=lambda f: (order.get(f.severity, 3), f.line or 0))
    print(f"发现 {len(findings)} 处问题（需人工复核，脚本不自动修改）：\n")
    for f in findings:
        loc = f"{args.file}:{f.line}" if f.line else args.file
        print(f"{loc} | [{f.severity}] {f.kind} — {f.entry}")
        print(f"    {f.detail}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
