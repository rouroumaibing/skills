#!/usr/bin/env python3
"""read_entry.py — 按节切片读取知识条目（只读，消费方与维护共用）。

契约要求消费方按**字节/token 预算**读条目，而不是无限整篇读入：先读「正常链路」
建立请求模型，再按命中环节只读「逐环节排查」对应小节与速查表相关行。本工具按
二级标题（## …）切片，输出指定节；--sections 省略时输出目录模式（frontmatter +
全部节标题 + 各节字节数），供先看结构再决定读哪节。

新增 --hop L6[,L7]：按环节号精准切片——输出逐环节排查里 - **L6/P6 开头的块、异常归因
速查里「环节」列为 L6 的行、流量走向里「环节」列为 L6 的行，以及这些环节依据的 R# 行。

用法
----
* python3 scripts/read_entry.py <entry.md>                              # 目录模式
* python3 scripts/read_entry.py <entry.md> --sections 正常链路,逐环节排查
* python3 scripts/read_entry.py <entry.md> --sections 异常归因速查 --max-bytes 2048
* python3 scripts/read_entry.py <entry.md> --hop L6                     # 按环节切片
* python3 scripts/read_entry.py <entry.md> --hop L6,P7 --max-bytes 2048
  （--sections 为二级标题前缀，逗号分隔；--hop 为环节编号，逗号分隔；--max-bytes 超限截断）
"""
from __future__ import annotations

import os
import re
import sys
import argparse


def die(reason: str) -> None:
    sys.stderr.write(f"status=ERROR msg={reason}\n")
    sys.exit(1)


def split_entry(path: str):
    """返回 (frontmatter_text, sections)；sections = [(标题行, 小节正文), …]。"""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        die(f"条目不存在或不可读: {path}")
    m = re.match(r"^(---\s*\n.*?\n---\s*\n)", text, re.S)
    fm_text = m.group(1) if m else ""
    rest = text[m.end():] if m else text
    sections: list[tuple[str, str]] = []
    cur_head, cur_lines = None, []
    for line in rest.splitlines(keepends=True):
        if re.match(r"^##\s+", line):
            if cur_head is not None:
                sections.append((cur_head, "".join(cur_lines)))
            cur_head, cur_lines = line.rstrip("\n"), []
        elif cur_head is not None:
            cur_lines.append(line)
    if cur_head is not None:
        sections.append((cur_head, "".join(cur_lines)))
    return fm_text, sections


def _section_body(sections: list[tuple[str, str]], prefix: str) -> str:
    for h, b in sections:
        if h.strip("# ").strip().startswith(prefix):
            return b
    return ""


def _table_rows(region: str) -> list[str]:
    """返回 region 里的表格数据行（去掉分隔行）。"""
    return [ln for ln in region.splitlines()
            if ln.strip().startswith("|") and not re.match(r"^\s*\|[\s:|-]+\|\s*$", ln)]


def _col_index(header: str, want: str) -> int:
    cols = [c.strip() for c in header.strip().strip("|").split("|")]
    return cols.index(want) if want in cols else (1 if want == "环节" else 0)


def hop_extract(sections: list[tuple[str, str]], hops: list[str]) -> str:
    """按环节号切片：逐环节块 + 速查/流量走向相关行 + 依据的 R# 行。"""
    hopset = {h.strip().upper() for h in hops if h.strip()}
    if not hopset:
        die("--hop 至少需要一个环节编号，如 L6")

    perhop = _section_body(sections, "逐环节排查")
    speed = _section_body(sections, "异常归因速查")
    normal = _section_body(sections, "正常链路")

    out: list[str] = []
    referenced_R: set[str] = set()

    # 1) 逐环节排查：- **L#/P# 开头的块（用 finditer 取完整块与环节号，避免多捕获组）
    if perhop:
        for m in re.finditer(r"(?m)^- \*\*(L\d+|P\d+)\b.*?(?=\n- \*\*|\Z)", perhop, flags=re.S):
            blk = m.group(0)
            hopid = m.group(1)
            if hopid.upper() in hopset:
                out.append(f"## 逐环节排查（环节 {hopid}）\n{blk.strip()}")
                referenced_R |= set(re.findall(r"R\d+", blk))

    # 2) 异常归因速查：环节列引用了目标环节的行
    if speed:
        rows = _table_rows(speed)
        header = next((ln for ln in rows if "环节" in ln and "现象" in ln), None)
        if header:
            idx = _col_index(header, "环节")
            for ln in rows[rows.index(header) + 1:]:
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if idx < len(cells) and any(r.upper() in hopset
                                           for r in re.findall(r"(L\d+|P\d+)", cells[idx])):
                    out.append("## 异常归因速查（相关行）\n" + ln)
                    referenced_R |= set(re.findall(r"R\d+", " ".join(cells)))

    # 3) 流量走向：环节列引用了目标环节的行
    if normal:
        rows = _table_rows(normal)
        header = next((ln for ln in rows if "环节" in ln and ("起点" in ln or "做什么" in ln)), None)
        if header:
            idx = _col_index(header, "环节")
            for ln in rows[rows.index(header) + 1:]:
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if idx < len(cells) and any(r.upper() in hopset
                                           for r in re.findall(r"(L\d+|P\d+)", cells[idx])):
                    out.append("## 流量走向（相关行）\n" + ln)
                    referenced_R |= set(re.findall(r"R\d+", " ".join(cells)))

    # 4) 资源匹配关系：被引用的 R# 行
    if normal and referenced_R:
        rows = _table_rows(normal)
        header = next((ln for ln in rows if "编号" in ln and "资源" in ln), None)
        if header:
            for ln in rows[rows.index(header) + 1:]:
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if cells and cells[0] in referenced_R:
                    out.append("## 资源匹配关系（被引用 R 行）\n" + ln)

    return "\n\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="按节/按环节切片读取知识条目（预算友好）")
    ap.add_argument("entry", help="条目文件路径")
    ap.add_argument("--sections", default=None,
                    help="要输出的节（二级标题前缀，逗号分隔；省略 = 目录模式）")
    ap.add_argument("--hop", default=None,
                    help="按环节编号切片（如 L6 或 L6,P7），逗号分隔")
    ap.add_argument("--max-bytes", type=int, default=None,
                    help="输出字节上限，超限截断并标注（默认不限制）")
    args = ap.parse_args()

    fm_text, sections = split_entry(args.entry)

    if args.hop:
        payload = hop_extract(sections, args.hop.split(","))
        if not payload:
            die(f"--hop {args.hop} 未匹配到任何环节（请检查编号是否存在于逐环节/速查/流量走向）")
    elif not args.sections:
        # 目录模式：frontmatter（供 confidence/scope 判断）+ 节标题与字节数
        sys.stdout.write(fm_text)
        sys.stdout.write("## 目录\n")
        for head, body in sections:
            sys.stdout.write(f"- {head.strip('# ').strip()}（{len(body.encode('utf-8'))} bytes）\n")
        total = sum(len(b.encode("utf-8")) for _, b in sections)
        sys.stdout.write(f"# 合计 {total} bytes，{len(sections)} 节\n")
        return
    else:
        wanted = [s.strip() for s in args.sections.split(",") if s.strip()]
        out = []
        for w in wanted:
            matched = [(h, b) for h, b in sections if h.strip("# ").strip().startswith(w)]
            if not matched:
                die(f"未找到节: {w}（可用节：{'；'.join(h.strip('# ').strip() for h, _ in sections)}）")
            if len(matched) > 1:
                names = "；".join(h.strip("# ").strip() for h, _ in matched)
                sys.stderr.write(f"warn: 节前缀「{w}」匹配到 {len(matched)} 个标题（{names}），"
                                 f"已全部输出；只需其一请写更长前缀\n")
            for h, b in matched:
                out.append(f"{h}\n{b}")
        payload = "".join(out)

    if args.max_bytes and len(payload.encode("utf-8")) > args.max_bytes:
        raw = payload.encode("utf-8")[: args.max_bytes].decode("utf-8", errors="ignore")
        payload = raw + "\n…（已按 --max-bytes 截断）\n"
    sys.stdout.write(payload)


if __name__ == "__main__":
    main()
