#!/usr/bin/env python3
"""validate.py — 知识条目校验器（知识层维护工具，非消费方）。

原名 build_index.py；index.yaml 机制已移除（2026-11-04）：条目量小、消费方本就
全量递归扫描，且「索引与全扫合并」存在隐患——索引里已删除的文件仍可作为候选命中
（消费方拿到不存在的路径）。故本工具只做校验，不再生成任何索引文件。

职责
----
* 启动即检查 PyYAML 依赖（缺失直接提示安装，不产出误导性的「解析失败」告警）；
* 递归扫描 knowledge/ 全部 *.md（oss/ 与 personal/ 两个 scope 子目录），解析 frontmatter；
* error → exit 1：
  - 必填字段（schema/domain/scope/updated/source/confidence/status/evidence）
  - schema 版本 = 1；日期格式 YYYY-MM-DD；枚举（scope/confidence/status/type，
    type 省略按 issue 处理）
  - scope 与所在目录一致；list 字段为字符串数组（数字型错误码必须加引号）
  - evidence 非空且每项含 run/what（covers 可选，字符串数组）——**没有证据的条目不收**
  - 条目内出现 HTML 注释（<!--）→ error：消费方整篇读入条目，注释内容模型照样
    能看到，注释是绕过扫描的隐蔽注入通道——知识条目一律禁止注释（模板/资产不受限）
  - {{ … }} 未填模板残留（跳过代码围栏）
  - 密钥特征 → error（AKIA… / BEGIN … PRIVATE KEY / Bearer … / password= 等）；
    扫描范围为全文（含代码围栏——围栏不是免责区）
  - oss 条目 superseded_by **或 related** 指向 personal/ → error（私有信息不出库）；
    related 指向自身 → error
  - 作废链循环引用 → error
* 告警（warn，不阻断）：
  - 私网 IP / 邮箱 / 手机号（手机号带前后非数字边界，防误伤时间戳等 11 位数字）
  - 注入句式（ignore previous / system prompt / 忽略以上 / 你现在是 / curl … | sh / …）
  - verified 条目正文含 〔推论〕/〔待复核〕行内标记（verified 不应混有未证实内容）
  - doc-sourced 条目缺版本信息（versions 为空且 evidence.run 不含版本号）
  - deprecated 缺 superseded_by；superseded_by 指向不存在或非 active 条目；
    related 指向不存在的条目
  - 体量超出 type 分级软上限（issue ≤100 行/5KB；system ≤300 行/20KB）
  - 重复错误码；updated 超过 180 天未更新（建议复核）

用法
----
* python3 scripts/validate.py                 # 校验（默认知识库根 = 本 skill 目录）
* python3 scripts/validate.py <knowledge_root>  # 指定知识库根
"""
from __future__ import annotations

import datetime
import os
import re
import sys
import argparse

try:
    import yaml  # noqa: F401
except ImportError:
    sys.stderr.write("status=ERROR msg=需要 PyYAML（pip install pyyaml）——前置依赖缺失，请先安装再运行\n")
    sys.exit(1)

SCHEMA_VERSION = 1
REQUIRED = ("domain", "scope", "updated", "source", "confidence", "status", "evidence")
LIST_FIELDS = ("components", "symptoms", "error_codes", "versions", "aliases", "envs", "related", "topics")
SCOPE_ENUM = {"oss", "personal"}
CONFIDENCE_ENUM = {"verified", "observed-once", "doc-sourced"}
STATUS_ENUM = {"active", "deprecated", "disputed"}
TYPE_ENUM = {"system", "issue"}          # 条目类型；省略按 issue 处理
# 体量软上限（行数, 字节数），按 type 分级；超限 warn（人工判断是否拆分，related 串联）
SIZE_LIMITS = {"issue": (100, 5 * 1024), "system": (300, 20 * 1024)}
STALE_DAYS = 180
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 密钥特征（error 级）——值若为 <占位符> 形态则视为已脱敏放行
SECRET_RES = [
    (r"AKIA[0-9A-Z]{16}", "AWS AccessKeyId"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥块"),
    (r"Bearer [A-Za-z0-9._~+/-]{20,}", "Bearer 凭据"),
    (r'(?i)(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\s*[=:]\s*["\']?(?!<)[^"\'\s<>]{6,}',
     "password/secret/token 赋值"),
]
# PII / 内部信息（warn 级）；手机号带前后非数字边界，防误伤时间戳等 11 位数字
PII_RES = [
    (r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
     "私网 IP"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "邮箱"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号"),
]
# 祈使 / 注入句式（warn 级）
INJECTION_RES = [
    (r"(?i)ignore\s+(?:all\s+)?previous", "ignore previous 指令覆盖"),
    (r"(?i)disregard\s+(?:\w+\s+)?instructions", "指令覆盖"),
    (r"(?i)ignore\s+\w*\s*instructions", "指令覆盖"),
    (r"(?i)system\s*prompt", "system prompt 提示词操纵"),
    (r"忽略(以上|之前|上述|前面|上面的|之前的)", "指令覆盖"),
    (r"无视(以上|之前|上述|前面)", "指令覆盖"),
    (r"你现在是", "角色重写"),
    (r"(?i)from\s+now\s+on,?\s+you\s+are", "角色重写"),
    (r"请执行", "祈使句"),
    (r"立即删除", "祈使句"),
    (r"rm\s+-rf\s+/", "危险删除命令"),
    (r"(?i)curl\b[^|]*\|\s*(sudo\s+)?(ba)?sh\b", "远程脚本执行"),
]

MARKERS = ("〔推论〕", "〔待复核〕")


def die(reason: str) -> None:
    sys.stderr.write(f"status=ERROR msg={reason}\n")
    sys.exit(1)


def strip_code_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


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


def read_full(path: str) -> str:
    """全文原文（不剥任何内容）——扫描范围含 frontmatter 与代码围栏。"""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def unfilled_marker(path: str) -> str | None:
    """正文是否残留 {{ … }} 未填模板标记（跳过代码围栏；注释已被注释禁令整体拒绝）。"""
    text = strip_code_fences(read_full(path))
    m = re.search(r"\{\{", text)
    return f"{{{{…}}}} 模板残留: {m.group(0)}@offset {m.start()}" if m else None


def scan_patterns(path: str, rel: str, patterns, level: str, errors, warnings) -> None:
    body = read_full(path)
    for pat, label in patterns:
        m = re.search(pat, body)
        if m:
            msg = f"{rel}: {level}: 检出{label}（{m.group(0)[:40]}…）"
            (errors if level == "error" else warnings).append(msg)


def build_entries(knowledge_dir: str):
    """扫描 + 校验。返回 (entries, errors, warnings)。"""
    errors: list[str] = []
    warnings: list[str] = []
    entries: list[dict] = []
    seen_codes: dict[tuple[str, str], str] = {}
    superseded_graph: dict[str, str] = {}
    related_graph: dict[str, list[str]] = {}
    today = datetime.date.today()

    files: list[str] = []
    for root, _dirs, names in os.walk(knowledge_dir):
        for fn in sorted(names):
            if fn.endswith(".md"):
                files.append(os.path.join(root, fn))

    for path in sorted(files):
        rel = os.path.relpath(path, knowledge_dir).replace("\\", "/")
        full = read_full(path)

        # HTML 注释禁令：条目里不允许任何注释（隐蔽注入通道，全文最先查）
        if "<!--" in full:
            errors.append(f"{rel}: 条目内含 HTML 注释（<!--）——注释对消费方模型可见，"
                          f"是绕过扫描的隐蔽注入通道，知识条目禁止注释")

        fm = parse_frontmatter(path)
        if not fm:
            errors.append(f"{rel}: frontmatter 缺失或不可解析")
            continue

        for k in REQUIRED:
            if not fm.get(k):
                errors.append(f"{rel}: 缺必填字段 {k}")

        if fm.get("schema") != SCHEMA_VERSION:
            errors.append(f"{rel}: schema 字段缺失或版本未知（当前 schema: {SCHEMA_VERSION}）")

        scope = str(fm.get("scope", "")).lower()
        if scope and scope not in SCOPE_ENUM:
            errors.append(f"{rel}: scope 非法: {scope}（应为 oss / personal）")
        top = rel.split("/", 1)[0] if "/" in rel else ""
        if top not in ("oss", "personal"):
            errors.append(f"{rel}: 条目必须放在 knowledge/oss/ 或 knowledge/personal/ 下")
        elif scope and scope != top:
            errors.append(f"{rel}: scope={scope} 与所在目录 {top}/ 不一致")

        if fm.get("updated") and not DATE_RE.match(str(fm["updated"])):
            errors.append(f"{rel}: updated 不是 YYYY-MM-DD: {fm['updated']}")

        conf = str(fm.get("confidence", "")).lower()
        if conf and conf not in CONFIDENCE_ENUM:
            errors.append(f"{rel}: confidence 非法: {conf}（应为 verified / observed-once / doc-sourced）")

        status = str(fm.get("status", "active")).lower()
        if fm.get("status") and status not in STATUS_ENUM:
            errors.append(f"{rel}: status 非法: {status}（应为 active / deprecated / disputed）")

        # 条目类型：system / issue（省略按 issue 处理），决定体量软上限
        etype = str(fm.get("type", "issue")).lower()
        if fm.get("type") and etype not in TYPE_ENUM:
            errors.append(f"{rel}: type 非法: {etype}（应为 system / issue，省略=issue）")
            etype = "issue"

        # evidence 必填：非空数组且每项含 run + what；covers 可选字符串数组
        ev = fm.get("evidence")
        if not isinstance(ev, list) or not ev:
            errors.append(f"{rel}: evidence 必须是非空数组（每项含 run/what）——无证据不收")
        else:
            for i, e in enumerate(ev):
                if not isinstance(e, dict) or not e.get("run") or not e.get("what"):
                    errors.append(f"{rel}: evidence[{i}] 缺 run 或 what")
                elif "covers" in e and (not isinstance(e["covers"], list)
                                        or not all(isinstance(x, str) for x in e["covers"])):
                    errors.append(f"{rel}: evidence[{i}].covers 必须是字符串数组")

        for k in LIST_FIELDS:
            v = fm.get(k)
            if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
                errors.append(f"{rel}: {k} 必须是字符串数组（数字型错误码/版本要加引号）")

        # 模板残留
        marker = unfilled_marker(path)
        if marker:
            errors.append(f"{rel}: {marker}")

        # 自动扫描：密钥 error；PII/注入 warn（全文含代码围栏）
        scan_patterns(path, rel, SECRET_RES, "error", errors, warnings)
        scan_patterns(path, rel, PII_RES, "warn", errors, warnings)
        scan_patterns(path, rel, INJECTION_RES, "warn", errors, warnings)

        # 行内标记与 confidence 的一致性
        found_markers = [mk for mk in MARKERS if mk in full]
        if conf == "verified" and found_markers:
            warnings.append(f"{rel}: confidence=verified 但正文含 {'/'.join(found_markers)} "
                            f"标记（verified 条目不应混有未证实内容）")

        # doc-sourced 必须带版本信息（versions 非空，或 evidence.run 含版本形态 v1 / 1.31；
        # 不能用「含任意数字」判定——gateway.networking.k8s.io 这类 URL 会误判）
        if conf == "doc-sourced":
            runs = " ".join(str((e or {}).get("run", "")) for e in (ev or []) if isinstance(e, dict))
            if not (fm.get("versions") or re.search(r"(v\d|\d+\.\d+)", runs)):
                warnings.append(f"{rel}: doc-sourced 条目缺版本信息"
                                f"（versions 为空且 evidence.run 不含版本号——上游行为随版本演进）")

        # 作废链
        sb = fm.get("superseded_by")
        if sb:
            sb_rel = str(sb).replace("\\", "/").lstrip("./")
            if status != "deprecated":
                warnings.append(f"{rel}: superseded_by 只在 status=deprecated 时有意义")
            if scope == "oss" and sb_rel.startswith("personal/"):
                errors.append(f"{rel}: oss 条目的 superseded_by 指向 personal/ 条目"
                              f"（{sb_rel}）——私有信息不出库")
            superseded_graph[rel] = sb_rel
        elif status == "deprecated":
            warnings.append(f"{rel}: deprecated 条目缺 superseded_by（应指向替代条目）")

        # related 关联条目：oss 禁指 personal/、禁自指；存在性在全文扫描后统一校验
        related_paths: list[str] = []
        rv = fm.get("related")
        if rv is not None:
            if isinstance(rv, list) and all(isinstance(x, str) for x in rv):
                related_paths = [str(x).replace("\\", "/").lstrip("./") for x in rv]
                for rp in related_paths:
                    if scope == "oss" and rp.startswith("personal/"):
                        errors.append(f"{rel}: oss 条目的 related 指向 personal/ 条目"
                                      f"（{rp}）——私有信息不出库")
                    if rp == rel:
                        errors.append(f"{rel}: related 指向自身（{rp}）")
                related_graph[rel] = related_paths
            # 非字符串数组的类型错误已由 LIST_FIELDS 统一报出

        # 体量软上限（按 type 分级）：超限 warn，人工判断是否按子系统拆分
        lim_lines, lim_bytes = SIZE_LIMITS[etype]
        n_lines = len(full.splitlines())
        n_bytes = len(full.encode("utf-8"))
        if n_lines > lim_lines or n_bytes > lim_bytes:
            warnings.append(f"{rel}: 体量 {n_lines} 行/{n_bytes}B 超出 type={etype} 软上限"
                            f"（{lim_lines} 行/{lim_bytes}B）——建议拆分并用 related 串联")

        # 过期告警
        if fm.get("updated") and DATE_RE.match(str(fm["updated"])):
            try:
                days = (today - datetime.date.fromisoformat(str(fm["updated"]))).days
                if days > STALE_DAYS:
                    warnings.append(f"{rel}: updated 已 {days} 天未更新（>{STALE_DAYS}），建议复核")
            except ValueError:
                pass

        # 重复项：同 domain 下同一错误码出现在多个条目（大小写不敏感）
        for code in (fm.get("error_codes") or []):
            key = (str(fm.get("domain", "")).lower(), str(code).lower())
            if key in seen_codes:
                warnings.append(f"重复错误码 {code}: {seen_codes[key]} 与 {rel}（确认是否有意并存，否则应合并）")
            else:
                seen_codes[key] = rel

        entries.append({
            "file": rel,
            "schema": fm.get("schema"),
            "type": etype,
            "domain": fm.get("domain"),
            "scope": scope,
            "components": fm.get("components") or [],
            "symptoms": fm.get("symptoms") or [],
            "error_codes": fm.get("error_codes") or [],
            "versions": fm.get("versions") or [],
            "aliases": fm.get("aliases") or [],
            "envs": fm.get("envs") or [],
            "status": status,
            "confidence": conf,
        })

    # superseded_by 指向校验 + 循环引用检测
    by_rel = {e["file"]: e for e in entries}
    for src, dst in superseded_graph.items():
        if dst not in by_rel:
            warnings.append(f"{src}: superseded_by 指向不存在的条目: {dst}")
        elif by_rel[dst]["status"] != "active":
            warnings.append(f"{src}: superseded_by 指向的条目 {dst} 不是 active（替代链断裂）")
    # related 指向校验（存在性；目标是否 active 不强求——关联≠替代）
    for src, dsts in related_graph.items():
        for dst in dsts:
            if dst not in by_rel:
                warnings.append(f"{src}: related 指向不存在的条目: {dst}")
    # 循环引用 → error
    for start in superseded_graph:
        seen, node = set(), start
        while node in superseded_graph and node not in seen:
            seen.add(node)
            node = superseded_graph[node]
            if node == start:
                errors.append(f"作废链循环引用: {' -> '.join(list(seen) + [start])}")
                break

    return entries, errors, warnings


def main() -> None:
    ap = argparse.ArgumentParser(description="知识条目校验器（index.yaml 机制已移除）")
    ap.add_argument("knowledge_root", nargs="?", default=None,
                    help="知识库根目录（默认：本 skill 目录，即 scripts/ 的上一级）")
    args = ap.parse_args()

    root = args.knowledge_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    knowledge_dir = os.path.join(root, "knowledge")
    if not os.path.isdir(knowledge_dir):
        die(f"条目目录不存在: {knowledge_dir}")

    entries, errors, warnings = build_entries(knowledge_dir)

    for w in warnings:
        sys.stderr.write(f"warn: {w}\n")
    if errors:
        for e in errors:
            sys.stderr.write(f"error: {e}\n")
        print(f"status=INVALID entries={len(entries)} errors={len(errors)} warnings={len(warnings)}")
        sys.exit(1)

    print(f"status=OK entries={len(entries)} warnings={len(warnings)}")


if __name__ == "__main__":
    main()
