#!/usr/bin/env python3
"""Detect change-narrative comments / doc lines that violate the
clean-change-comments convention (rule 1).

The script only *flags* candidates for human review — it never edits files.
False positives are expected and acceptable, because the goal is to surface
lines worth a second look, not to auto-delete.

Usage:
    python detect_change_narrative.py <file-or-dir> [--json] [--ext go,ts,py,md,mjs,vue,makefile,sql,yaml,sh]

Exit code is 0 when no candidates are found, 1 when at least one is found
(so it can be wired into CI as a soft check).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import List, Optional

# ---------------------------------------------------------------------------
# Change-narrative marker patterns (rule 1 forbidden narration).
# Conservative: these describe a *transition* ("was X, now Y"), which is what
# the convention bans from code comments and doc bodies.
# ---------------------------------------------------------------------------
MARKERS: List[re.Pattern[str]] = [
    # original-state narration
    re.compile(r"原来是|原是|原本|原先|起初|以前是|之前是|以前用|之前用|以前这里|之前一直"),
    # change-action narration
    re.compile(r"改为|改成|修改为|已改|改成了|改为了|换成了|替换成|替换为|由[一-龥]+改|从[一-龥]+改"),
    # stop / deprecate narration
    re.compile(r"不再|已不再|不再使用|不再支持|不再需要|已废弃|已弃用|废弃|弃用|过时|旧版|老版本|老做法|旧做法|旧逻辑|旧实现|老的实现"),
    # replace-instruction narration
    re.compile(r"禁止.{0,6}改用|改用|不要再用|切勿|不要再|不应该再|禁止再|须改用|统一改用"),
    # action-retrospective narration
    re.compile(r"新增了|添加了?|删除了?|移除了?|重构了?|优化了?|重写了?|调整为|迁移到|迁到|升级到|已修复|修复了|修复之前|修复后"),
    # English equivalents
    re.compile(r"previously|used to|was using|changed from|no longer|deprecated|refactored|migrated to|replaced with|now uses|instead of", re.IGNORECASE),
]

# Decision-date leak: a date stamped together with a decision marker, which
# must live ONLY in 变更历史/CHANGELOG — never in body, titles, subtitles,
# status lines, or inline (rule 4). Examples: （2026-10-20 设计已定） /
# （2026-09-28 裁定） / （2026-10-08 修正）. Both word-before-date and
# word-after-date orderings inside the parentheses are matched.
DECISION_DATE_RE = re.compile(
    r"[（(][^）)\n]{0,18}?20\d\d-\d\d-\d\d[^）)\n]{0,18}?"
    r"(裁定|修正|拍板|设计已定|设计裁定|决策|已定|钢人裁定|落地|已落地)"
    r"[^）)\n]{0,10}?[）)]"
    r"|"
    r"[（(][^）)\n]{0,10}?(裁定|修正|拍板|设计已定|设计裁定|决策|已定|钢人裁定|落地|已落地)"
    r"[^）)\n]{0,18}?20\d\d-\d\d-\d\d[^）)\n]{0,18}?[）)]"
)
# Bare date inside parentheses in body/status-lines (e.g. `状态（2026-10-20）`)
# — also a decision-date leak when no changelog context surrounds it.
BARE_DATE_RE = re.compile(r"[（(]20\d\d-\d\d-\d\d[）)]")
# A heading (title / subtitle / section) must never carry a date in parentheses.
HEADING_DATE_RE = re.compile(r"[（(]20\d\d-\d\d-\d\d")
MARKERS.extend([DECISION_DATE_RE, BARE_DATE_RE])

# Headings whose *content* is a legitimate home for history/decisions — skip
# their bodies when scanning Markdown.
SKIP_SECTION_RE = re.compile(
    r"变更记录|变更历史|更新日志|changelog|决策记录|决策日志|decision|adr|架构决策",
    re.IGNORECASE,
)

# Comment styles by file extension.
LINE_COMMENT = {
    "go": "//", "ts": "//", "tsx": "//", "js": "//", "jsx": "//",
    "mjs": "//", "cjs": "//",
    "java": "//", "c": "//", "cpp": "//", "h": "//", "cs": "//",
    "swift": "//", "rs": "//", "scala": "//", "kt": "//", "m": "//",
    "py": "#", "sh": "#", "bash": "#", "makefile": "#", "yml": "#", "yaml": "#",
    "toml": "#", "cfg": "#", "ini": "#", "dockerfile": "#", "rb": "#",
    "pl": "#", "r": "#",
    "sql": "--", "lua": "--",
    "tex": "%",
}
BLOCK_COMMENT = {"go", "ts", "tsx", "js", "jsx", "mjs", "cjs",
                 "java", "c", "cpp", "h", "cs", "swift", "rs", "scala", "kt", "m"}
HTML_LIKE = {"html", "xml", "vue"}

# Makefiles are matched by filename (they usually have no extension), not by
# extension. They use "#" comments, same as shell.
MAKE_NAMES = {"makefile", "gnumakefile", "kbuild"}
MD_EXTS = {"md", "markdown"}

DEFAULT_EXTS = sorted(set(LINE_COMMENT) | BLOCK_COMMENT | HTML_LIKE | MD_EXTS
                      | {"sql", "lua", "tex"})

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build",
             "__pycache__", "target", ".next", ".workbuddy"}


def _ext_of(path: str) -> str:
    """Return the scan key for a path.

    Uses the file extension, except for Makefiles which are matched by
    basename (they usually have no extension).
    """
    base = os.path.basename(path).lower()
    if base in MAKE_NAMES:
        return "makefile"
    return path.rsplit(".", 1)[-1].lower() if "." in path else ""


@dataclass
class Hit:
    path: str
    line_no: int
    pattern: str
    text: str


def _strip_block(text: str) -> str:
    """Pull /* ... */ content out of a code line (single-line blocks)."""
    out = []
    for m in re.finditer(r"/\*(.*?)\*/", text):
        out.append(m.group(1))
    return "\n".join(out) if out else ""


def _comment_text(code_line: str, ext: str) -> str:
    """Best-effort extraction of comment text from a code line.

    Returns the concatenated comment portions; empty string if none found.
    Not string-literal aware — good enough for flagging, since every hit is
    reviewed by a human.
    """
    line = code_line
    parts: List[str] = []

    if ext in HTML_LIKE:
        for m in re.finditer(r"<!--(.*?)-->", line):
            parts.append(m.group(1))
        return "\n".join(parts)

    # block comment content first
    blk = _strip_block(line)
    if blk:
        parts.append(blk)
        line = re.sub(r"/\*.*?\*/", " ", line)

    marker = LINE_COMMENT.get(ext)
    if marker:
        # avoid treating "://" / "http://" / "file://" as a comment start
        # by temporarily masking url-like sequences
        masked = re.sub(r"(https?|file)://", lambda m: m.group(0).replace("//", "§§"), line)
        idx = masked.find(marker)
        if idx != -1:
            # ensure not inside a word for '#' style (e.g. "#" in "foo#bar")
            if marker == "#" and idx > 0 and (line[idx - 1].isalnum() or line[idx - 1] == "_"):
                pass
            else:
                parts.append(line[idx + len(marker):])

    return "\n".join(p.strip() for p in parts if p.strip())


def _md_bodies(lines: List[str]):
    """Yield (line_no, text) for non-skipped markdown bodies.

    Tracks headings; skips the body of sections whose heading matches
    SKIP_SECTION_RE (变更记录 / 决策记录 / CHANGELOG / ADR ...).
    """
    skipping = False
    for i, raw in enumerate(lines, start=1):
        heading = re.match(r"^#{1,6}\s+(.*)$", raw)
        if heading:
            title = heading.group(1)
            skipping = bool(SKIP_SECTION_RE.search(title))
            # A heading (title / subtitle / section) must not carry a date in
            # parentheses — the date belongs in the 变更历史 chapter, not in
            # the heading. Flag non-skip headings that leak one (rule 4).
            if not skipping and HEADING_DATE_RE.search(title):
                yield i, raw
            continue
        # setext heading (underline of = or -)
        if re.match(r"^[-=]{3,}\s*$", raw.strip()):
            continue
        if skipping:
            continue
        yield i, raw


def _scan_vue(lines: List[str]):
    """For .vue files: scan both <!-- --> HTML comments and // /* */ comments
    inside <script> blocks. Yields (line_no, comment_text) with real file line
    numbers so hits stay traceable.

    Vue files embed a <template> (HTML) and a <script> (JS/TS); the generic
    comment extractor only sees HTML comments, so script-block comments need a
    dedicated pass.
    """
    in_script = False
    for i, raw in enumerate(lines, start=1):
        line = raw
        # HTML comments anywhere in the file
        for m in re.finditer(r"<!--(.*?)-->", line):
            yield i, m.group(1)
        open_m = re.search(r"<script\b[^>]*>", line)
        close_m = re.search(r"</script\s*>", line)
        if open_m and not close_m:
            in_script = True
            after = line[open_m.end():]
            ct = _comment_text(after, "ts")
            if ct:
                yield i, ct
        elif close_m and not open_m:
            in_script = False
            before = line[:close_m.start()]
            ct = _comment_text(before, "ts")
            if ct:
                yield i, ct
        elif open_m and close_m:
            between = line[open_m.end():close_m.start()]
            ct = _comment_text(between, "ts")
            if ct:
                yield i, ct
        elif in_script:
            ct = _comment_text(line, "ts")
            if ct:
                yield i, ct


def scan_file(path: str, ext: str) -> List[Hit]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return []

    hits: List[Hit] = []
    lines = content.splitlines()

    if ext in MD_EXTS:
        targets = _md_bodies(lines)
    elif ext == "vue":
        targets = _scan_vue(lines)
    else:
        targets = ((i, _comment_text(line, ext)) for i, line in enumerate(lines, start=1))

    for line_no, text in targets:
        if not text:
            continue
        for pat in MARKERS:
            m = pat.search(text)
            if m:
                hits.append(Hit(path=path, line_no=line_no,
                                pattern=pat.pattern, text=text.strip()))
                break  # one pattern label per line is enough
    return hits


def collect_paths(root: str, exts: List[str]) -> List[str]:
    results: List[str] = []
    if os.path.isfile(root):
        results.append(root)
        return results
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = _ext_of(os.path.join(dirpath, fn))
            if ext in exts:
                results.append(os.path.join(dirpath, fn))
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="files or directories to scan")
    parser.add_argument("--ext", default=",".join(DEFAULT_EXTS),
                        help="comma-separated extensions to scan (default: common source exts)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    exts = [e.strip().lower().lstrip(".") for e in args.ext.split(",") if e.strip()]
    all_hits: List[Hit] = []

    for root in args.paths:
        for path in collect_paths(root, exts):
            ext = _ext_of(path)
            all_hits.extend(scan_file(path, ext))

    if args.json:
        payload = [{"path": h.path, "line": h.line_no,
                    "pattern": h.pattern, "text": h.text} for h in all_hits]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not all_hits:
            print("✓ 未发现变更叙述类注释候选。")
        else:
            print(f"发现 {len(all_hits)} 处候选（需人工复核，脚本不自动修改）：\n")
            for h in all_hits:
                print(f"{h.path}:{h.line_no} | 命中[{h.pattern}]")
                print(f"    {h.text}\n")

    return 1 if all_hits else 0


if __name__ == "__main__":
    sys.exit(main())
