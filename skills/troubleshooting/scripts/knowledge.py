#!/usr/bin/env python3
"""知识库自动发现（只读）：供 troubleshooting 河道在定位问题时「取水」。

设计要点（河道与河流）
--------------------
* 河道只通过本脚本「扫描 / 检索」知识库，**绝不写、绝不改** knowledge/ 下任何文件；
* **不写死读哪个文件**：路由完全由查询向量驱动，新增 / 删除知识条目无需改本脚本；
* 条目按 scope 分两层：`knowledge/oss/`（开源通用，可分发）与 `knowledge/personal/`
  （个人项目私有）——递归扫描两层都覆盖，`--scope` 可过滤；
* 默认只取水 active 条目；命中按「分数降序 → confidence 优先级（verified >
  observed-once > doc-sourced）」排序，取 top N（预算防上下文撑爆；条目较长时
  配合知识层的 scripts/read_entry.py 按节读取）；
* 知识库位置来自 troubleshooting 的 config.yaml 的 `troubleshooting-knowledge`
  （跨平台路径，复用 lib.expand_path）；若指向知识库 skill 根目录则取其下的
  `knowledge/`，若已直接指向 `knowledge/` 目录本身则直接用；
* index.yaml 机制已移除（2026-11-04）：条目量小、全量递归扫描即足够，且
  「索引与全扫合并」存在已删除文件仍可命中的隐患。
"""
from __future__ import annotations

import os
import re
import sys
import argparse

try:
    import yaml  # noqa: F401  # 前置依赖：缺失时给出明确安装提示，而非误导性的解析告警
except ImportError:
    sys.stderr.write("status=ERROR msg=需要 PyYAML（pip install pyyaml）——前置依赖缺失，请先安装再运行\n")
    sys.exit(1)

# 复用 troubleshooting 的 config / 路径展开（单一真相源，避免重复实现）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

# 参与匹配的 frontmatter 字段（topics 为旧 schema 兼容保留）
MATCH_FIELDS = ("domain", "components", "symptoms", "error_codes",
                "versions", "aliases", "envs", "topics")
CONF_RANK = {"verified": 0, "observed-once": 1, "doc-sourced": 2}
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def die(reason: str) -> None:
    sys.stderr.write(f"status=ERROR msg={reason}\n")
    sys.exit(1)


def find_knowledge_dir() -> str:
    config = lib.load_config()
    knowledge_base = (config.get("troubleshooting-knowledge") or "").strip()
    if not knowledge_base:
        die("config.troubleshooting-knowledge 未配置（河道无水可取）")
    knowledge_base_root = lib.expand_path(knowledge_base)
    knowledge_base_root = os.path.expanduser(knowledge_base_root).replace("\\", "/")
    if os.path.basename(knowledge_base_root.rstrip("/")) == "knowledge":
        knowledge_dir = knowledge_base_root
    else:
        knowledge_dir = os.path.join(knowledge_base_root, "knowledge")
    if not os.path.isdir(knowledge_dir):
        die(f"知识库目录不存在: {knowledge_dir}")
    return knowledge_dir


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


def collect_candidates(knowledge_dir: str) -> dict:
    """全量递归扫描（含 oss/ 与 personal/ 子目录），返回 {path: frontmatter}。"""
    seen: dict[str, dict] = {}
    for root, _dirs, files in os.walk(knowledge_dir):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(root, fn)
            seen[path] = parse_frontmatter(path)
    return seen


def _cjk_bigrams(term: str) -> list[str]:
    """含中文的查询词取 2-gram（中文自然语句整词匹配不上时的回退）。"""
    if not CJK_RE.search(term) or len(term) < 2:
        return []
    return list({term[i:i + 2] for i in range(len(term) - 1)})


def score_entry(path: str, fm: dict, haystack: list[str]) -> tuple[int, str]:
    """按 frontmatter 语义字段 + 正文匹配查询向量（大小写不敏感）；返回 (score, body)。

    打分：整词命中 fm +3 / 正文 +1；中文查询词整词未命中时按 2-gram 重合度回退，
    权重低于整词（fm +2 / 正文 +1，且需 ≥2 个 gram 命中才计分）。
    """
    parts: list[str] = []
    for k in MATCH_FIELDS:
        v = fm.get(k)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    fm_text = " ".join(parts).lower()
    try:
        body = open(path, encoding="utf-8").read()
    except OSError:
        body = ""
    body_lower = body.lower()
    score = 0
    for q in haystack:
        ql = q.lower()
        fm_hit = ql in fm_text
        body_hit = ql in body_lower
        if fm_hit:
            score += 3
        if body_hit:
            score += 1
        if not fm_hit and not body_hit:
            grams = _cjk_bigrams(ql)
            if grams:
                hits_fm = sum(1 for g in grams if g in fm_text)
                hits_body = sum(1 for g in grams if g in body_lower)
                if hits_fm >= 2:
                    score += 2
                if hits_body >= 2:
                    score += 1
    return score, body


def main() -> None:
    ap = argparse.ArgumentParser(description="知识库自动发现（只读）")
    ap.add_argument("--query", help="自由文本查询（组件/版本/错误码/症状混合）")
    ap.add_argument("--component", help="组件名（domain）")
    ap.add_argument("--error", help="错误码 / 状态")
    ap.add_argument("--version", help="版本")
    ap.add_argument("--scope", choices=["oss", "personal"], default=None,
                    help="按层级过滤条目（oss=开源通用 / personal=个人项目），不指定则两层都取")
    ap.add_argument("--list", action="store_true", help="列出全部条目路径")
    ap.add_argument("--path-only", action="store_true", help="只输出命中文件路径")
    ap.add_argument("--top", type=int, default=5, help="最多返回条目数（预算，默认 5）")
    ap.add_argument("--include-inactive", action="store_true",
                    help="包含非 active 条目（deprecated/disputed）——默认只取水 active")
    args = ap.parse_args()

    knowledge_dir = find_knowledge_dir()

    def usable(fm: dict) -> bool:
        """默认只消费 active 条目（deprecated/disputed 不取水），--include-inactive 放开；
        --scope 过滤层级（oss/personal），不指定则两层都取。"""
        if not args.include_inactive and str(fm.get("status", "active")).lower() != "active":
            return False
        if args.scope and str(fm.get("scope", "")).lower() != args.scope:
            return False
        return True

    if args.list:
        for path, fm in sorted(collect_candidates(knowledge_dir).items()):
            if usable(fm):
                print(path)
        return

    haystack: list[str] = []
    for v in (args.query, args.component, args.error, args.version):
        if v:
            haystack.extend(re.split(r"[\s,;/]+", v.strip()))
    haystack = [h for h in haystack if h]
    if not haystack:
        die("未提供任何查询（--query/--component/--error/--version 至少给一个，或 --list）")

    ranked = []
    for path, fm in collect_candidates(knowledge_dir).items():
        if not usable(fm):
            continue
        score, body = score_entry(path, fm, haystack)
        if score > 0:
            conf = str(fm.get("confidence", "")).lower()
            ranked.append((score, CONF_RANK.get(conf, 3), path, body))
    # 排序：分数降序 → confidence 优先级（verified > observed-once > doc-sourced）→ 路径
    ranked.sort(key=lambda x: (-x[0], x[1], x[2]))

    if not ranked:
        print("status=NO_KNOWLEDGE_HIT query=%s" % " ".join(haystack))
        return

    for score, _cr, path, body in ranked[: max(1, args.top)]:
        if args.path_only:
            print(path)
        else:
            print(f"# === {path} (score={score}) ===")
            print(body)
            print()


if __name__ == "__main__":
    main()
