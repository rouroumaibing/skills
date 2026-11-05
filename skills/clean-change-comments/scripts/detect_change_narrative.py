#!/usr/bin/env python3
"""Detect change-narrative comments / doc lines that violate the
clean-change-comments convention.

The script only *flags* candidates for human review — it never edits files.
False positives are expected and acceptable, because the goal is to surface
lines worth a second look, not to auto-delete.

Usage:
    python detect_change_narrative.py <file-or-dir> [--json] [--ext go,ts,py,md,mjs,vue,makefile,sql,yaml,sh]
    python detect_change_narrative.py <file-or-dir> --diff [BASE]   # only lines added by git diff BASE (default HEAD)

In --diff mode every newly-added comment line is flagged as a candidate that
must be classified against the why-comment exception (防回退 / 非直觉约束) or removed;
lines matching a narration marker are labeled with that marker.

Recall-only `[待归类]` labels surface comment shapes for human classification;
they are never error-level judgements. Exit code is 0 when no candidates
are found, 1 when at least one is found (CI soft check).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Change-narrative marker patterns: conservative transitions ("was X, now Y") only.
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

# Decision-date leak: dates belong only in 变更历史/CHANGELOG; both word-date orders match.
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

# ---------------------------------------------------------------------------
# Config-comment markers: config comments describe only user-facing fields, not internals.
# ---------------------------------------------------------------------------
CONFIG_EXTS = {"yaml", "yml", "toml", "ini", "cfg"}
CONFIG_VERBOSE_MARKERS: List[re.Pattern[str]] = [
    re.compile(r"顺序[：:].*[→>]"),                  # detection order with arrows = internal algorithm
    re.compile(r"NODE_IP|POD_IP"),                   # concrete internal env var names
    re.compile(r"默认路由出口"),                        # internal detection mechanism
    re.compile(r"探测失败兜底|失败兜底|兜底监听"),          # fallback-logic narration
    re.compile(r"bind-interfaces|listen-address"),    # dnsmasq internal fragments
    re.compile(r"restrict default|fudge \d"),         # ntp internal fragments
    re.compile(r"k8s 环境变量|环境变量（|环境变量\)"),       # internal env-var explanation
    re.compile(r"匹配语义|相邻新增行|已知盲区|白名单会漏检|历史教训|探测顺序|目录名跳过"),
]

# Recall-only markers: mechanically matchable, but whether a comment is
# justified is a human call, so these never become error-level hits.
PRINCIPLE_MARKERS: List[Tuple[str, re.Pattern[str]]] = [
    ("[指路导读]", re.compile(r"见\s*README|详见|参见|见下|见上|见\s*docstring|无需配置|see README|refer to", re.I)),
    ("[实现复述]", re.compile(r"遍历|依次|逐行|逐项|先.{0,24}再|然后|最后|拼接|折叠空白|兜底|回退到")),
    ("[时间/版本标记]", re.compile(r"\bv\d+\s*(?:[:：]|起|加固|对齐|前|后)|20\d\d-\d\d-\d\d|解析值")),
]
LONG_COMMENT_BLOCK_MIN = 4

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
            # A heading date belongs in 变更历史, never in the title.
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


def _long_comment_blocks(lines: List[str], ext: str) -> List[int]:
    """First line of each run of >= N non-decorative comment lines."""
    if ext in MD_EXTS or ext == "vue":
        return []
    marker = LINE_COMMENT.get(ext, "#")
    allowed = set(marker + " -=*#")
    out: List[int] = []
    run: List[int] = []
    for i, raw in enumerate(lines, start=1):
        s = raw.strip()
        is_comment = s.startswith(marker)
        decorative = is_comment and set(s) <= allowed
        if is_comment and not decorative:
            run.append(i)
        else:
            if len(run) >= LONG_COMMENT_BLOCK_MIN:
                out.append(run[0])
            run = []
    if len(run) >= LONG_COMMENT_BLOCK_MIN:
        out.append(run[0])
    return out


def scan_file(path: str, ext: str) -> List[Hit]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return []

    hits: List[Hit] = []
    lines = content.splitlines()

    if ext in MD_EXTS:
        targets = list(_md_bodies(lines))
    elif ext == "vue":
        targets = list(_scan_vue(lines))
    else:
        targets = [(i, _comment_text(line, ext)) for i, line in enumerate(lines, start=1)]

    for line_no, text in targets:
        if not text:
            continue
        for pat in MARKERS:
            m = pat.search(text)
            if m:
                hits.append(Hit(path=path, line_no=line_no,
                                pattern=pat.pattern, text=text.strip()))
                break  # one pattern label per line is enough

    # Config comments leaking implementation internals: flag only, never edit.
    if ext in CONFIG_EXTS:
        for line_no, text in targets:
            if not text:
                continue
            for pat in CONFIG_VERBOSE_MARKERS:
                if pat.search(text):
                    hits.append(Hit(path=path, line_no=line_no,
                                    pattern="[配置冗余]" + pat.pattern, text=text.strip()))
                    break

    # Recall only: surface code-comment shapes for human classification, never judge or edit.
    # Markdown bodies are prose, not comments, so principle markers do not apply there.
    reported = {h.line_no for h in hits}
    if ext not in MD_EXTS:
        for line_no, text in targets:
            if not text or line_no in reported:
                continue
            label = next((lbl for lbl, pat in PRINCIPLE_MARKERS if pat.search(text)), None)
            if label:
                hits.append(Hit(path=path, line_no=line_no,
                                pattern="[待归类]" + label, text=text.strip()))
                reported.add(line_no)
        for line_no in _long_comment_blocks(lines, ext):
            if line_no not in reported:
                hits.append(Hit(path=path, line_no=line_no,
                                pattern="[待归类][多行注释块]", text=lines[line_no - 1].strip()))
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


def _git_root(path: str) -> Optional[str]:
    """Return the git repository root containing *path*, or None."""
    p = path if os.path.isdir(path) else (os.path.dirname(path) or ".")
    root = os.path.abspath(p)
    while True:
        if os.path.isdir(os.path.join(root, ".git")):
            return root
        parent = os.path.dirname(root)
        if parent == root:
            return None
        root = parent


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Newly-added comments with no narration marker still need human classification.
NEW_COMMENT_LABEL = "新增注释·待归类(按核心原则)"


def scan_diff(paths: List[str], base: str, exts: List[str]) -> List[Hit]:
    """Flag comment lines added by `git diff <base>` under *paths*.

    Necessity of a comment cannot be judged mechanically, so the gate is
    presence itself: every newly-added comment line becomes a candidate that
    must be classified against the core principle or removed. Lines matching a
    narration marker are labeled with that marker instead. Markdown body lines are
    content rather than comments, so only narration / decision-date matches
    are flagged there.
    """
    import subprocess

    hits: List[Hit] = []
    for root in paths:
        repo = _git_root(root)
        if repo is None:
            print(f"（--diff 跳过 {root}：不在 git 仓库内）")
            continue
        spec = ["git", "-C", repo, "diff", "-U0", "--no-color", base, "--"]
        rel = os.path.relpath(os.path.abspath(root), repo)
        if rel != ".":
            spec.append(rel)
        try:
            proc = subprocess.run(spec, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"（--diff 跳过 {root}：git diff 失败 {exc}）")
            continue
        cur_path: Optional[str] = None
        new_ln = 0
        for line in proc.stdout.splitlines():
            if line.startswith("diff --git"):
                m = re.search(r" b/(.+)$", line)
                cur_path = m.group(1) if m else None
                new_ln = 0
            elif line.startswith("+++ "):
                m = re.match(r"\+\+\+ b/(.*)$", line)
                cur_path = m.group(1) if m else None
                new_ln = 0
            elif line.startswith("--- ") or line.startswith("index "):
                continue
            elif _HUNK_RE.match(line):
                new_ln = int(_HUNK_RE.match(line).group(1))
            elif cur_path is not None and line.startswith("+"):
                new_ln += 1
                body = line[1:]
                ext = _ext_of(cur_path)
                if ext not in exts:
                    continue
                if ext in MD_EXTS:
                    matched = next((p for p in MARKERS if p.search(body)), None)
                    if matched:
                        hits.append(Hit(path=os.path.join(repo, cur_path),
                                        line_no=new_ln, pattern=matched.pattern,
                                        text=body.strip()))
                    continue
                if ext == "vue":
                    parts = [mm.group(1) for mm in re.finditer(r"<!--(.*?)-->", body)]
                    ct = _comment_text(body, "ts")
                    if ct:
                        parts.append(ct)
                    ct = "\n".join(p.strip() for p in parts if p.strip())
                else:
                    ct = _comment_text(body, ext)
                if not ct:
                    continue
                matched = next((p for p in MARKERS if p.search(ct)), None)
                if matched is None and ext in CONFIG_EXTS:
                    cvm = next((p for p in CONFIG_VERBOSE_MARKERS if p.search(ct)), None)
                    if cvm is not None:
                        hits.append(Hit(path=os.path.join(repo, cur_path), line_no=new_ln,
                                        pattern="[配置冗余]" + cvm.pattern, text=ct.strip()))
                        continue
                if matched is None:
                    review = next((lbl for lbl, p in PRINCIPLE_MARKERS if p.search(ct)), None)
                    label = "[待归类]" + review if review else NEW_COMMENT_LABEL
                else:
                    label = matched.pattern
                hits.append(Hit(path=os.path.join(repo, cur_path), line_no=new_ln,
                                pattern=label, text=ct.strip()))
            # "-" (deleted) and " " (context) lines never advance new_ln
    return hits


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="files or directories to scan")
    parser.add_argument("--ext", default=",".join(DEFAULT_EXTS),
                        help="comma-separated extensions to scan (default: common source exts)")
    parser.add_argument("--diff", nargs="?", const="HEAD", default=None, metavar="BASE",
                        help="scan only lines added by `git diff BASE` (default BASE=HEAD) "
                             "under the given paths, instead of whole files")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    exts = [e.strip().lower().lstrip(".") for e in args.ext.split(",") if e.strip()]
    all_hits: List[Hit] = []

    if args.diff is not None:
        all_hits.extend(scan_diff(args.paths, args.diff, exts))
    else:
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
