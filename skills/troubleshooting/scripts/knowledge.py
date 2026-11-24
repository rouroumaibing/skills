#!/usr/bin/env python3
"""知识库自动发现（只读）：供 troubleshooting 河道在定位问题时「取水」。

设计要点（河道与河流）
--------------------
* 河道只通过本脚本「扫描 / 检索」知识库，**绝不写、绝不改** knowledge/ 下任何文件；
* **唯一检索入口**：打分 / 排序 / 词边界防护 / 命中遥测全部收口在知识库 skill 的
  `scripts/search.py` CLI（集成契约，见 troubleshooting-knowledge 的
  `references/consumer-contract.md`）。本脚本只做**薄委托**：解析 config 拿知识库根
  → subprocess 调 search.py → 解析其稳定输出行 → 读正文打印。**禁止在消费侧复刻
  打分实现**（2026-11-24 实测：复刻版缺短 ASCII 词边界防护已漂移，且绕过命中遥测）；
* **不写死读哪个文件**：路由完全由查询向量驱动，新增 / 删除知识条目无需改本脚本；
* 条目按 scope 分两层：`knowledge/oss/`（开源通用，可分发）与 `knowledge/personal/`
  （个人项目私有）——递归扫描两层都覆盖，`--scope` 可过滤；
* 默认只取水 active 条目；命中按「分数降序 → confidence 优先级（verified >
  observed-once > doc-sourced）」排序，取 top N（预算防上下文撑爆；条目较长时
  配合知识层的 scripts/read_entry.py 按节读取）；
* 命中遥测：search.py CLI 检索自动落盘到知识库根 `.usage-log.jsonl`——**取水即被
  记录**，「复核按命中触发」的数据来源；
* 知识库位置来自 troubleshooting 的 config.yaml 的 `troubleshooting-knowledge`
  （跨平台路径，复用 lib.expand_path）；须指向知识库 **skill 根**（或其 knowledge/
  目录），且根下须有 `scripts/search.py`——集成契约的物理载体；
* index.yaml 机制已移除（2026-11-04）：条目量小、全量递归扫描即足够，且
  「索引与全扫合并」存在已删除文件仍可命中的隐患。
"""
from __future__ import annotations

import os
import re
import sys
import argparse
import subprocess

try:
    import yaml  # noqa: F401  # 前置依赖：缺失时给出明确安装提示，而非误导性的解析告警
except ImportError:
    sys.stderr.write("status=ERROR msg=需要 PyYAML（pip install pyyaml）——前置依赖缺失，请先安装再运行\n")
    sys.exit(1)

# 复用 troubleshooting 的 config / 路径展开（单一真相源，避免重复实现）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

# search.py CLI 命中行格式（集成契约，变更须同步契约冒烟测试）：
#   score=<int> hit=<fm|body|fm+body|2gram> scope=<s> conf=<c> status=<s> <rel>
HIT_LINE_RE = re.compile(r"^score=(\d+) hit=\S+ scope=\S* conf=\S+ status=\S+ (\S+)$")


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
    """全量递归扫描（含 oss/ 与 personal/ 子目录），返回 {path: frontmatter}。

    仅 --list 使用（列表非检索，不涉打分契约）。
    """
    seen: dict[str, dict] = {}
    for root, _dirs, files in os.walk(knowledge_dir):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(root, fn)
            seen[path] = parse_frontmatter(path)
    return seen


def search_via_cli(knowledge_dir: str, terms: list[str], args) -> list[tuple[int, str]]:
    """薄委托知识库 skill 的 search.py CLI（唯一检索入口）。

    返回 [(score, 绝对路径)]，顺序即 CLI 排序结果（分数降序 → confidence → 路径）。
    打分 / 防护 / 遥测随 CLI 演进，本函数不复刻任何匹配语义。
    """
    root = os.path.dirname(knowledge_dir)
    search_py = os.path.join(root, "scripts", "search.py")
    if not os.path.isfile(search_py):
        die(f"知识库 skill 缺少 scripts/search.py（集成契约要求唯一检索入口）: {search_py}"
            "——请更新 troubleshooting-knowledge")
    cmd = [sys.executable, search_py, *terms,
           "--root", root, "--top", str(max(1, args.top))]
    if args.scope:
        cmd += ["--scope", args.scope]
    if args.include_inactive:
        cmd += ["--include-inactive"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        die(f"search.py 检索失败（exit {proc.returncode}）——知识库 skill 环境异常")
    hits: list[tuple[int, str]] = []
    for line in proc.stdout.splitlines():
        m = HIT_LINE_RE.match(line.strip())
        if not m:
            continue
        path = os.path.normpath(os.path.join(
            knowledge_dir, m.group(2).replace("\\", "/")))
        hits.append((int(m.group(1)), path))
    if not hits and not proc.stdout.lstrip().startswith("status=NO_KNOWLEDGE_HIT"):
        die("search.py 输出不符合集成契约（既无命中行也无 NO_KNOWLEDGE_HIT）"
            "——两侧 skill 版本可能不匹配，请更新 troubleshooting-knowledge")
    return hits


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
        """--list 的过滤（检索路径的过滤由 search.py CLI 负责）：
        默认只列 active 条目，--include-inactive 放开；--scope 过滤层级。"""
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

    hits = search_via_cli(knowledge_dir, haystack, args)

    if not hits:
        print("status=NO_KNOWLEDGE_HIT query=%s" % " ".join(haystack))
        return

    for score, path in hits:
        if args.path_only:
            print(path)
            continue
        try:
            body = open(path, encoding="utf-8").read()
        except OSError:
            body = ""
        print(f"# === {path} (score={score}) ===")
        print(body)
        print()


if __name__ == "__main__":
    main()
