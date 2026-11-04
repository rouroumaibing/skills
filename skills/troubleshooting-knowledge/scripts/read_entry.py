#!/usr/bin/env python3
"""read_entry.py — 按节切片读取知识条目（只读，消费方与维护共用）。

契约要求消费方按**字节/token 预算**读条目，而不是无限整篇读入：先读「正常链路」
建立请求模型，再按命中环节只读「逐环节排查」对应小节与速查表相关行。本工具按
二级标题（## …）切片，输出指定节；--sections 省略时输出目录模式（frontmatter +
全部节标题 + 各节字节数），供先看结构再决定读哪节。

用法
----
* python3 scripts/read_entry.py <entry.md>                    # 目录模式
* python3 scripts/read_entry.py <entry.md> --sections 正常链路,逐环节排查
* python3 scripts/read_entry.py <entry.md> --sections 异常归因速查 --max-bytes 2048
  （--sections 为二级标题前缀匹配，逗号分隔；--max-bytes 超限截断并标注）
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


def main() -> None:
    ap = argparse.ArgumentParser(description="按节切片读取知识条目（预算友好）")
    ap.add_argument("entry", help="条目文件路径")
    ap.add_argument("--sections", default=None,
                    help="要输出的节（二级标题前缀，逗号分隔；省略 = 目录模式）")
    ap.add_argument("--max-bytes", type=int, default=None,
                    help="输出字节上限，超限截断并标注（默认不限制）")
    args = ap.parse_args()

    fm_text, sections = split_entry(args.entry)

    if not args.sections:
        # 目录模式：frontmatter（供 confidence/scope 判断）+ 节标题与字节数
        sys.stdout.write(fm_text)
        sys.stdout.write("## 目录\n")
        for head, body in sections:
            sys.stdout.write(f"- {head.strip('# ').strip()}（{len(body.encode('utf-8'))} bytes）\n")
        total = sum(len(b.encode("utf-8")) for _, b in sections)
        sys.stdout.write(f"# 合计 {total} bytes，{len(sections)} 节\n")
        return

    wanted = [s.strip() for s in args.sections.split(",") if s.strip()]
    out: list[str] = []
    for w in wanted:
        matched = [(h, b) for h, b in sections if h.strip("# ").strip().startswith(w)]
        if not matched:
            die(f"未找到节: {w}（可用节：{'；'.join(h.strip('# ').strip() for h, _ in sections)}）")
        for h, b in matched:
            out.append(f"{h}\n{b}")

    payload = "".join(out)
    if args.max_bytes and len(payload.encode("utf-8")) > args.max_bytes:
        raw = payload.encode("utf-8")[: args.max_bytes].decode("utf-8", errors="ignore")
        payload = raw + "\n…（已按 --max-bytes 截断）\n"
    sys.stdout.write(payload)


if __name__ == "__main__":
    main()
