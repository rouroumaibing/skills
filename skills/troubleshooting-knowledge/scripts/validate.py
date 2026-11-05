#!/usr/bin/env python3
"""validate.py — 知识条目校验器（知识层维护工具，非消费方）。

原名 build_index.py；index.yaml 机制已移除（2026-11-04）：条目量小、消费方本就
全量递归扫描，且「索引与全扫合并」存在隐患——索引里已删除的文件仍可作为候选命中
（消费方拿到不存在的路径）。故本工具只做校验，不再生成任何索引文件。

职责
----
* 启动即检查 PyYAML 依赖（缺失直接提示安装，不产出误导性的「解析失败」告警）；
* 校验 knowledge/ 全部 *.md（oss/ 与 personal/ 两个 scope 子目录），解析 frontmatter；
  也支持**落盘前**校验草稿：--file <路径> --scope oss|personal，或
  --draft-dir <目录> [--scope X]，给出与落盘后一致的结果（条目不进 knowledge/ 即可先校验）；
* 过期判断基准日可用 --today 固定（默认当天），便于测试；
* error → exit 1；warn → 不阻断。

error（阻断）
- 必填字段 / schema=1 / 日期 / 枚举（scope/confidence/status/type）
- scope 与所在目录一致；list 字段为字符串数组（数字型错误码/版本加引号）
- evidence 非空且每项含 run/what（covers 可选字符串数组）——无证据不收
- 条目含 HTML 注释（<!--）→ error（隐蔽注入通道，消费方整篇读入，注释对模型可见）
- 未填模板标记：{{填写…}} 全文（含代码围栏）检测；泛 {{ 跳过围栏仍检测
- 高置信度密钥特征 error：AKIA… / 私钥块 / Bearer≥20 字符 / password|passwd|pwd= 后接字面值
- oss 条目 superseded_by 或 related 指向 personal/ → error；related 自指 → error
- 作废链循环引用 → error
- frontmatter 不可解析且文件仍含 {{ 标记 → 报「模板未填完」（而非 frontmatter 缺失）

warn（不阻断）
- 泛词密钥（secret/token/api_key 赋值）降为 warn（已加词边界，排除 imagePullSecret/secretName 等）
- 私网 IP / 邮箱 / 手机号（手机号带前后非数字边界，防误伤时间戳等 11 位数字）
- 注入句式（ignore previous / system prompt / 忽略以上 / 你现在是 / curl … | sh / …）
- verified 条目正文含 〔推论〕/〔待复核〕行内标记
- doc-sourced 缺版本信息（versions 为空且 evidence.run 不含版本形态）
- deprecated 缺 superseded_by；superseded_by/related 指向不存在的条目
- 体量超出 type 分级软上限（issue ≤100 行/5KB；system ≤300 行/20KB）
- 重复错误码（关联条目 system↔issue 或双向 related 豁免）
- 正文缺核心节（正常链路/逐环节排查/异常归因速查；system 另需 是什么/组件与架构）
- 锚点完整性（warn 级，误报代价低）：L/P 已定义未覆盖、引用未定义 R#、速查环节列引用未定义编号
- updated 超过 180 天未更新（建议复核）

用法
----
* python3 scripts/validate.py                             # 校验 knowledge/（默认本 skill 目录）
* python3 scripts/validate.py <knowledge_root>            # 指定知识库根
* python3 scripts/validate.py --file /tmp/kb-draft/x.md --scope oss   # 落盘前校验草稿
* python3 scripts/validate.py --draft-dir /tmp/kb-draft --scope oss
* python3 scripts/validate.py --today 2026-11-05          # 固定过期判断基准日（测试用）
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

# 高置信度密钥特征（error 级）——泛词（secret/token）已降为 warn（见下）
SECRET_ERROR_RES = [
    (r"AKIA[0-9A-Z]{16}", "AWS AccessKeyId"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥块"),
    (r"Bearer [A-Za-z0-9._~+/-]{20,}", "Bearer 凭据(≥20字符)"),
    # password/passwd/pwd 赋值；词边界 (?![A-Za-z]) 排除嵌入长词；后接字面值 ≥6
    (r"(?<![A-Za-z])(?:password|passwd|pwd)\s*[=:]\s*[\"']?(?!<)[^\"'\s<>]{6,}", "password/passwd 赋值"),
]
# 泛词密钥（warn 级）：secret/token/api_key/access_token 的赋值。
# 词边界 (?![A-Za-z]) 排除 imagePullSecret、secretName 等词内情形；kind: Secret 后无 =/: 不会命中。
SECRET_WARN_RES = [
    (r"(?<![A-Za-z])(?:secret|token|api[_-]?key|access[_-]?token)\s*[=:]\s*[\"']?(?!<)[^\"'\s<>]{6,}",
     "secret/token 赋值(泛词)"),
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
    """未填模板标记检测。

    - 具体前缀 {{填写 在**全文（含代码围栏）**检测：模板占位符即便写在 mermaid
      围栏里也要抓出（验收：围栏内 {{填写：组件关系图}} 报 error）；
    - 泛 {{ 仍跳过代码围栏（mermaid 的 A{{判断}} 是合法语法，误报代价高）。
    """
    full = read_full(path)
    m = re.search(r"\{\{填写", full)
    if m:
        return f"{{{{填写…}}}} 模板残留（含代码围栏）: {m.group(0)}@offset {m.start()}"
    body = strip_code_fences(full)
    m2 = re.search(r"\{\{", body)
    if m2:
        return f"{{{{…}}}} 模板残留: {m2.group(0)}@offset {m2.start()}"
    return None


def scan_patterns(path: str, rel: str, patterns, level: str, errors, warnings) -> None:
    body = read_full(path)
    for pat, label in patterns:
        m = re.search(pat, body)
        if m:
            msg = f"{rel}: {level}: 检出{label}（{m.group(0)[:40]}…）"
            (errors if level == "error" else warnings).append(msg)


def extract_sections(text: str) -> dict[str, str]:
    """按二级标题（## …）切块，返回 {标题去空格: 小节正文}。"""
    regions: dict[str, str] = {}
    cur_key: str | None = None
    cur_lines: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            if cur_key is not None:
                regions[cur_key] = "\n".join(cur_lines)
            cur_key = m.group(1).strip()
            cur_lines = []
        elif cur_key is not None:
            cur_lines.append(line)
    if cur_key is not None:
        regions[cur_key] = "\n".join(cur_lines)
    return regions


def section_by_prefix(regions: dict[str, str], prefix: str) -> str:
    for k, v in regions.items():
        if k.startswith(prefix):
            return v
    return ""


def structure_checks(rel: str, full: str, etype: str, warnings: list[str]) -> None:
    """正文结构校验（warn）：核心三节必填；system 另需 是什么/组件与架构。"""
    headings = list(extract_sections(full).keys())
    core = ["正常链路", "逐环节排查", "异常归因速查"]
    for c in core:
        if not any(h.startswith(c) for h in headings):
            warnings.append(f"{rel}: 正文缺核心节「{c}」（模板固定结构：正常链路→逐环节排查→异常归因速查）")
    if etype == "system":
        for c in ["是什么", "组件与架构"]:
            if not any(h.startswith(c) for h in headings):
                warnings.append(f"{rel}: type=system 缺节「{c}」（issue 可省略）")


def anchor_checks(rel: str, full: str, warnings: list[str]) -> None:
    """锚点完整性校验（warn 级，误报代价低）。

    已定义编号取表格首列：资源匹配关系表首列 = R#；流量走向/控制面下发表首列 = L#/P#。
    规避代码围栏里的 mermaid（|--| 不在行首，不进首列正则）。
    """
    body = strip_code_fences(full)            # 去掉 mermaid 噪音，避免 L#/R# 误判
    regions = extract_sections(body)

    def first_cells(region: str) -> list[str]:
        return re.findall(r"(?m)^\s*\|\s*([A-Za-z]\d+)\s*\|", region)

    normal = section_by_prefix(regions, "正常链路")
    perhop = section_by_prefix(regions, "逐环节排查")
    speed = section_by_prefix(regions, "异常归因速查")

    defined_R = {c for c in first_cells(normal) if c.startswith("R")}
    defined_LP = {c for c in first_cells(normal) if c[0] in ("L", "P")}

    # 已覆盖的 L/P：逐环节排查的块头 - **L6 / 以及正文提及（围栏已剥）
    covered_LP: set[str] = set()
    if perhop:
        covered_LP |= set(re.findall(r"(?m)^- \*\*(L\d+|P\d+)", perhop))
        covered_LP |= set(re.findall(r"\b(L\d+|P\d+)\b", perhop))

    # 1) L/P 已定义但未覆盖
    for x in sorted(defined_LP - covered_LP):
        warnings.append(f"{rel}: 锚点: {x} 已定义但未在「逐环节排查」覆盖")

    # 2) 正文引用了未定义的 R#
    referenced_R = set(re.findall(r"\bR\d+\b", body))
    for x in sorted(referenced_R - defined_R):
        warnings.append(f"{rel}: 锚点: 正文引用了未定义的 {x}（资源匹配关系表缺该编号）")

    # 3) 速查表「环节」列引用未定义编号
    if speed:
        lines = [ln for ln in speed.splitlines() if ln.strip().startswith("|")]
        header = next((ln for ln in lines if "环节" in ln and "现象" in ln), None)
        if header:
            cols = [c.strip() for c in header.strip().strip("|").split("|")]
            idx = cols.index("环节") if "环节" in cols else 1
            for ln in lines[lines.index(header) + 1:]:
                if re.match(r"^\s*\|[\s:|-]+\|\s*$", ln):
                    continue  # 分隔行
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if idx < len(cells):
                    for ref in re.findall(r"(L\d+|P\d+)", cells[idx]):
                        if ref not in defined_LP:
                            warnings.append(f"{rel}: 锚点: 速查表「环节」列引用了未定义的 {ref}")


def _dup_exempt(first_rel: str, second_rel: str, related_graph: dict[str, list[str]],
                entries_by_rel: dict[str, dict], cur_type: str, cur_domain: str) -> bool:
    """重复错误码豁免：双向 related，或 一 system 一 issue 且同 domain。

    注意：second_rel（当前条目）尚未写入 entries_by_rel（在去重循环之后才追加），
    故当前条目的 type/domain 由调用方显式传入，只查 first_rel 的归档信息。
    """
    if (second_rel in related_graph.get(first_rel, [])
            and first_rel in related_graph.get(second_rel, [])):
        return True
    a = entries_by_rel.get(first_rel)
    if (a and {a.get("type"), cur_type} == {"system", "issue"}
            and a.get("domain") == cur_domain):
        return True
    return False


def collect_pairs(knowledge_dir: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for root, _dirs, names in os.walk(knowledge_dir):
        for fn in sorted(names):
            if fn.endswith(".md"):
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, knowledge_dir).replace("\\", "/")
                pairs.append((p, rel))
    return sorted(pairs)


def build_entries(pairs: list[tuple[str, str]], today: datetime.date):
    """扫描 + 校验。pairs = [(path, rel), …]。返回 (entries, errors, warnings)。"""
    errors: list[str] = []
    warnings: list[str] = []
    entries: list[dict] = []
    seen_codes: dict[tuple[str, str], str] = {}
    superseded_graph: dict[str, str] = {}
    related_graph: dict[str, list[str]] = {}
    entries_by_rel: dict[str, dict] = {}

    for path, rel in pairs:
        full = read_full(path)

        # HTML 注释禁令：条目里不允许任何注释（隐蔽注入通道，全文最先查）
        if "<!--" in full:
            errors.append(f"{rel}: 条目内含 HTML 注释（<!--）——注释对消费方模型可见，"
                          f"是绕过扫描的隐蔽注入通道，知识条目禁止注释")

        fm = parse_frontmatter(path)
        if not fm:
            if "{{" in full:
                errors.append(f"{rel}: 模板未填完——仍含 {{填写：…}} 标记未替换，"
                              f"frontmatter 可能被占位符破坏（请先替换全部 {{填写：…}}）")
            else:
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

        # 自动扫描：高置信度密钥 error；泛词密钥/PII/注入 warn（全文含代码围栏）
        scan_patterns(path, rel, SECRET_ERROR_RES, "error", errors, warnings)
        scan_patterns(path, rel, SECRET_WARN_RES, "warn", errors, warnings)
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

        # 正文结构校验（warn）
        structure_checks(rel, full, etype, warnings)
        # 锚点完整性校验（warn 级）
        anchor_checks(rel, full, warnings)

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

        # 重复项：同 domain 下同一错误码出现在多个条目（大小写不敏感），关联豁免
        domain_lower = str(fm.get("domain", "")).lower()
        for code in (fm.get("error_codes") or []):
            key = (domain_lower, str(code).lower())
            if key in seen_codes:
                first_rel = seen_codes[key]
                if not _dup_exempt(first_rel, rel, related_graph, entries_by_rel, etype, domain_lower):
                    warnings.append(f"重复错误码 {code}: {first_rel} 与 {rel}（确认是否有意并存，否则应合并）")
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
        entries_by_rel[rel] = {"type": etype, "domain": domain_lower}

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


def _parse_today(s: str | None) -> datetime.date:
    if s:
        try:
            return datetime.date.fromisoformat(s)
        except ValueError:
            die(f"--today 格式应为 YYYY-MM-DD，得到: {s}")
    return datetime.date.today()


def main() -> None:
    ap = argparse.ArgumentParser(description="知识条目校验器（index.yaml 机制已移除）")
    ap.add_argument("knowledge_root", nargs="?", default=None,
                    help="知识库根目录（默认：本 skill 目录，即 scripts/ 的上一级）")
    ap.add_argument("--file", default=None,
                    help="落盘前校验单个草稿文件（需搭配 --scope）")
    ap.add_argument("--draft-dir", default=None,
                    help="落盘前校验整个草稿目录（可搭配 --scope；否则按目录名推断 oss/personal）")
    ap.add_argument("--scope", choices=["oss", "personal"], default=None,
                    help="草稿的 scope（与 --file / --draft-dir 搭配）")
    ap.add_argument("--today", default=None,
                    help="固定过期判断基准日 YYYY-MM-DD（默认当天，测试用）")
    args = ap.parse_args()

    today = _parse_today(args.today)

    if args.file:
        if not args.scope:
            die("--file 需要搭配 --scope oss|personal")
        if not os.path.isfile(args.file):
            die(f"草稿不存在: {args.file}")
        pairs = [(args.file, f"{args.scope}/{os.path.basename(args.file)}")]
    elif args.draft_dir:
        if not os.path.isdir(args.draft_dir):
            die(f"草稿目录不存在: {args.draft_dir}")
        scope = args.scope or os.path.basename(args.draft_dir.rstrip("/"))
        if scope not in SCOPE_ENUM:
            die(f"无法推断 draft-dir 的 scope，且未提供 --scope（得到 {scope}）")
        pairs = [(os.path.join(args.draft_dir, f), f"{scope}/{f}")
                 for f in sorted(os.listdir(args.draft_dir)) if f.endswith(".md")]
    else:
        root = args.knowledge_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        knowledge_dir = os.path.join(root, "knowledge")
        if not os.path.isdir(knowledge_dir):
            die(f"条目目录不存在: {knowledge_dir}")
        pairs = collect_pairs(knowledge_dir)

    entries, errors, warnings = build_entries(pairs, today)

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
