#!/usr/bin/env python3
"""search.py — 知识库检索 / 查重（知识层维护工具，行为可复现）。

「先查重」与「知识库里有没有 X」共用本脚本：同一套打分与排序，结果可复现。

打分：frontmatter 语义字段（domain/components/symptoms/error_codes/versions/
aliases/envs，兼容旧 topics）权重 3 + 正文权重 1，大小写不敏感；中文查询词整词
未命中时按 2-gram 重合度回退（权重低于整词，需 ≥2 个 gram 命中）。
排序：分数降序 → confidence 优先级（verified > observed-once > doc-sourced）→ 路径。
过滤：默认只出 active 条目（--include-inactive 放开）；--scope oss|personal 分层过滤。

用法
----
* python3 scripts/search.py <关键词…> [--scope oss|personal] [--top N] [--include-inactive] [--root <知识库根>]
  （--root 省略时默认为本 skill 目录，即 scripts/ 的上一级）
"""
from __future__ import annotations

import os
import re
import sys
import argparse

try:
    import yaml  # noqa: F401
except ImportError:
    sys.stderr.write("status=ERROR msg=需要 PyYAML（pip install pyyaml）——前置依赖缺失，请先安装再运行\n")
    sys.exit(1)

MATCH_FIELDS = ("domain", "components", "symptoms", "error_codes",
                "versions", "aliases", "envs", "topics")  # topics 为旧 schema 兼容
CONF_RANK = {"verified": 0, "observed-once": 1, "doc-sourced": 2}
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def die(reason: str) -> None:
    sys.stderr.write(f"status=ERROR msg={reason}\n")
    sys.exit(1)


def parse_frontmatter(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"warn: 解析 frontmatter 失败 {path}: {exc}\n")
        return {}
    return data if isinstance(data, dict) else {}


def iter_entries(knowledge_dir: str):
    for root, _dirs, files in os.walk(knowledge_dir):
        for fn in sorted(files):
            if fn.endswith(".md"):
                path = os.path.join(root, fn)
                yield path, parse_frontmatter(path)


def _cjk_bigrams(term: str) -> list[str]:
    """含中文的查询词取 2-gram（中文自然语句整词匹配不上时的回退）。"""
    if not CJK_RE.search(term) or len(term) < 2:
        return []
    return list({term[i:i + 2] for i in range(len(term) - 1)})


def score_entry(fm: dict, body: str, terms: list[str]) -> int:
    """打分：整词命中 fm +3 / 正文 +1；中文查询词整词未命中时按 2-gram 重合度
    回退，权重低于整词（fm +2 / 正文 +1，且需 ≥2 个 gram 命中才计分）。"""
    parts: list[str] = []
    for k in MATCH_FIELDS:
        v = fm.get(k)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    fm_text = " ".join(parts).lower()
    body_lower = body.lower()
    score = 0
    for t in terms:
        tl = t.lower()
        # 长度≤3 的纯 ASCII 词按词边界匹配，避免 gat 误中 gateway、503 误中 5031 等；
        # 其余（含中文、长词）保持子串匹配，中文未命中再走 2-gram 回退。
        short_ascii = (len(tl) <= 3 and tl.isascii())
        if short_ascii:
            pat = r"\b" + re.escape(tl) + r"\b"
            fm_hit = bool(re.search(pat, fm_text))
            body_hit = bool(re.search(pat, body_lower))
        else:
            fm_hit = tl in fm_text
            body_hit = tl in body_lower
        if fm_hit:
            score += 3
        if body_hit:
            score += 1
        if not fm_hit and not body_hit:
            grams = _cjk_bigrams(tl)
            if grams:
                hits_fm = sum(1 for g in grams if g in fm_text)
                hits_body = sum(1 for g in grams if g in body_lower)
                if hits_fm >= 2:
                    score += 2
                if hits_body >= 2:
                    score += 1
    return score


def main() -> None:
    ap = argparse.ArgumentParser(description="知识库检索 / 查重（打分可复现）")
    ap.add_argument("terms", nargs="+", help="查询关键词（组件/错误码/症状/别名混合，大小写不敏感）")
    ap.add_argument("--root", default=None,
                    help="知识库根目录（默认：本 skill 目录，即 scripts/ 的上一级）")
    ap.add_argument("--scope", choices=["oss", "personal"], default=None,
                    help="按层级过滤（oss=开源通用 / personal=个人项目）")
    ap.add_argument("--top", type=int, default=5, help="最多返回条目数（默认 5）")
    ap.add_argument("--include-inactive", action="store_true",
                    help="包含非 active 条目——默认只列 active")
    args = ap.parse_args()

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    knowledge_dir = os.path.join(root, "knowledge")
    if not os.path.isdir(knowledge_dir):
        die(f"条目目录不存在: {knowledge_dir}")

    terms = []
    for t in args.terms:
        terms.extend(x for x in re.split(r"[\s,;/]+", t.strip()) if x)
    if not terms:
        die("未提供任何查询关键词")

    ranked = []
    for path, fm in iter_entries(knowledge_dir):
        status = str(fm.get("status", "active")).lower()
        if not args.include_inactive and status != "active":
            continue
        scope = str(fm.get("scope", "")).lower()
        if args.scope and scope != args.scope:
            continue
        try:
            body = open(path, encoding="utf-8").read()
        except OSError:
            body = ""
        score = score_entry(fm, body, terms)
        if score > 0:
            rel = os.path.relpath(path, knowledge_dir).replace("\\", "/")
            conf = str(fm.get("confidence", "")).lower()
            ranked.append((score, CONF_RANK.get(conf, 3), rel, scope, conf, status,
                           fm.get("updated", "")))
    ranked.sort(key=lambda x: (-x[0], x[1], x[2]))

    if not ranked:
        print("status=NO_KNOWLEDGE_HIT terms=%s" % " ".join(terms))
        return

    for score, _cr, rel, scope, conf, status, updated in ranked[: max(1, args.top)]:
        print(f"score={score} scope={scope} conf={conf} status={status} updated={updated} {rel}")


if __name__ == "__main__":
    main()
