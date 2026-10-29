#!/usr/bin/env python3
"""Detect assertion-strength regressions in test code.

The rule this script enforces (assertion-strength conservation): an agent that
is trying to make a case pass may silently weaken its assertions — swapping an
exact comparison for a substring check, dropping a field assertion, or
swallowing the exception. A prose rule is too soft to catch that, because the
weakening looks exactly like ordinary progress.

The script compares the *before* and *after* state of the same subject
expression. When a strong assertion about subject S disappears and a weaker
assertion about the same S appears, that is a downgrade worth a human look.

The script only *reports* candidates — it never edits files. False positives
are expected and acceptable: the goal is to surface lines worth a second look,
not to auto-fix.

Usage:
    python check_assertion_strength.py <path> [<path> ...]            # whole-file scan
    python check_assertion_strength.py <path> [<path> ...] --diff [BASE]   # diff vs BASE (default HEAD)
    python check_assertion_strength.py --diff-file <patch> <path> ...  # analyze a unified diff file

Exit code is 0 when no candidate is found, 1 when at least one is found
(so it can be wired into CI as a soft check).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

STRONG = "STRONG"
WEAK = "WEAK"

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__",
             "target", ".next", ".workbuddy", ".venv", "venv"}

# ---------------------------------------------------------------------------
# Assertion forms.
#
# Each entry is (compiled regex, strength, subject strategy).
#   subject strategy:
#     "group:<name>"  -> use that regex group as the subject expression
#     "args"          -> split the call arguments and pick the first one that
#                        is neither a literal nor a framework context arg
# ---------------------------------------------------------------------------

_CTX_ARGS = {"self", "this", "t", "b", "tt", "ctx", "msg", "message",
             "context", "testcase", "test", "spec"}

_LITERAL_RE = re.compile(
    r"""^(?:
          '[^']*'          # single-quoted
        | "[^"]*"          # double-quoted
        | `[^`]*`          # backtick
        | -?\d+(?:\.\d+)?  # number
        | (?:true|false|null|none|nil|undefined|True|False|None|NULL)
        )$""",
    re.VERBOSE,
)

# Python unittest
_PY_STRONG = re.compile(
    r"\b(?P<fn>assert(?:Equal|NotEqual|DictEqual|ListEqual|SetEqual|TupleEqual|"
    r"SequenceEqual|MultiLineEqual|CountEqual))\s*\((?P<args>.*)\)\s*$")
# `assertIn(member, container)` — the container is the subject.
_PY_IN = re.compile(
    r"\b(?P<fn>assert(?:In|NotIn))\s*\((?P<args>.*)\)\s*$")
# `assertRegex(text, pattern)` — the text is the subject and the pattern is
# the expected value, so the subject is the *first* argument here.
_PY_REGEX = re.compile(
    r"\b(?P<fn>assert(?:Regex|NotRegex))\s*\((?P<args>.*)\)\s*$")
_PY_TRUTHY = re.compile(
    r"\b(?P<fn>assert(?:True|False|IsNone|IsNotNone))\s*\((?P<args>.*)\)\s*$")
_PY_IS = re.compile(
    r"\b(?P<fn>assert(?:Is|IsNot))\s*\((?P<args>.*)\)\s*$")
# pytest
_PYTEST_EQ = re.compile(r"^\s*assert\s+(?P<left>.+?)\s*(?P<op>==|!=)\s*(?P<right>.+?)\s*$")
_PYTEST_TRUTHY = re.compile(r"^\s*assert\s+(?P<expr>.+?)\s*$")
# JS / TS
_JEST_STRONG = re.compile(
    r"\bexpect\s*\(\s*(?P<actual>.+?)\s*\)\s*\.\s*(?:not\s*\.\s*)?"
    r"(toBe|toEqual|toStrictEqual)\s*\(")
_JEST_WEAK = re.compile(
    r"\bexpect\s*\(\s*(?P<actual>.+?)\s*\)\s*\.\s*(?:not\s*\.\s*)?"
    r"(toContain|toMatch|toMatchObject|toBeTruthy|toBeFalsy|toBeDefined|"
    r"toBeUndefined|toHaveLength|toHaveProperty|toBeGreaterThan|toBeLessThan)\s*\(")
# Go testify
_GO_STRONG = re.compile(
    r"\b(?:assert|require)\s*\.\s*(?P<fn>Equal|EqualValues|Equalf|Exactly|"
    r"NotEqual|NotEqualf)\s*\((?P<args>.*)\)\s*$")
_GO_WEAK = re.compile(
    r"\b(?:assert|require)\s*\.\s*(?P<fn>True|False|"
    r"NotNil|Nil)\s*\((?P<args>.*)\)\s*$")
# testify's `Regexp(t, rx, str)` — the string being matched is the subject.
_GO_REGEXP = re.compile(
    r"\b(?:assert|require)\s*\.\s*(?P<fn>Regexp|RegexpEqual)\s*\((?P<args>.*)\)\s*$")
# testify's `Contains(t, element, container)` — the *container* is the
# subject being asserted about; the element is the expected content.
_GO_CONTAINS = re.compile(
    r"\b(?:assert|require)\s*\.\s*(?P<fn>Contains|NotContains)\s*\((?P<args>.*)\)\s*$")
_GO_IF_STRONG = re.compile(
    r"^\s*if\s+(?P<left>.+?)\s*!=\s*(?P<right>.+?)\s*\{\s*t\s*\.\s*"
    r"(?:Errorf|Fatalf|Error|Fatal)\(")
_GO_IF_WEAK = re.compile(
    r"^\s*if\s+(?P<left>.+?)\s*==\s*(?P<right>.+?)\s*\{\s*t\s*\.\s*"
    r"(?:Errorf|Fatalf|Error|Fatal)\(")
# JUnit / AssertJ
_JUNIT_STRONG = re.compile(
    r"\b(?P<fn>assertEquals|assertArrayEquals|assertSame|assertNotSame|"
    r"assertNotEquals)\s*\((?P<args>.*)\)\s*$")
_JUNIT_WEAK = re.compile(
    r"\b(?P<fn>assertTrue|assertFalse|assertNull|assertNotNull|assertThat)\s*\((?P<args>.*)\)\s*$")
_ASSERTJ_STRONG = re.compile(
    r"\bassertThat\s*\(\s*(?P<actual>.+?)\s*\)\s*\.\s*"
    r"(?P<fn>isEqualTo|isEqualToComparingTo|isSameAs|containsExactly)\s*\(")
_ASSERTJ_WEAK = re.compile(
    r"\bassertThat\s*\(\s*(?P<actual>.+?)\s*\)\s*\.\s*"
    r"(?P<fn>contains|containsSequence|hasSize|isNotNull|isGreaterThan)\s*\(")
# chai / cypress
_CHAI_STRONG = re.compile(
    r"\bexpect\s*\(\s*(?P<actual>.+?)\s*\)\s*\.\s*to\s*\.\s*(?:equal|eql)\s*\(")
_CHAI_WEAK = re.compile(
    r"\bexpect\s*\(\s*(?P<actual>.+?)\s*\)\s*\.\s*to\s*\.\s*"
    r"(?:contain|include|match)\s*\(")

# ---------------------------------------------------------------------------
# Assertion forms.
#
# Each entry is (compiled regex, strength, subject spec).
#
# Subject specs — the subject is the expression a human would name when
# saying "the assertion about the order id". Getting this wrong makes the
# diff comparison blind, so each framework is spelled out explicitly:
#
#   ("group", <name>)        use that regex group
#   ("args", "last")         the last argument that is neither a literal
#                            (the expected value) nor a context argument
#                            (`self`, `t`, `msg`, ...)
#   ("args", "index", <n>)   the n-th argument, counting from 0
# ---------------------------------------------------------------------------

FORMS: List[Tuple[re.Pattern, str, tuple]] = [
    (_PYTEST_EQ, STRONG, ("group", "left")),
    (_PYTEST_TRUTHY, WEAK, ("group", "expr")),
    (_JEST_STRONG, STRONG, ("group", "actual")),
    (_JEST_WEAK, WEAK, ("group", "actual")),
    (_GO_IF_STRONG, STRONG, ("group", "left")),
    (_GO_IF_WEAK, WEAK, ("group", "left")),
    (_ASSERTJ_STRONG, STRONG, ("group", "actual")),
    (_ASSERTJ_WEAK, WEAK, ("group", "actual")),
    (_CHAI_STRONG, STRONG, ("group", "actual")),
    (_CHAI_WEAK, WEAK, ("group", "actual")),
    (_PY_STRONG, STRONG, ("args", "last")),
    (_PY_IS, STRONG, ("args", "last")),
    (_PY_IN, WEAK, ("args", "index", 1)),
    (_PY_REGEX, WEAK, ("args", "index", 0)),
    (_PY_TRUTHY, WEAK, ("args", "index", 0)),
    (_GO_STRONG, STRONG, ("args", "index", 2)),
    (_GO_WEAK, WEAK, ("args", "index", 1)),
    (_GO_REGEXP, STRONG, ("args", "index", 2)),
    (_GO_CONTAINS, WEAK, ("args", "index", 2)),
    (_JUNIT_STRONG, STRONG, ("args", "index", 1)),
    (_JUNIT_WEAK, WEAK, ("args", "index", 0)),
]

# A trailing containment-style call hides the real subject; strip it so that
# `resp.getCode().contains("2")` and `resp.getCode()` compare as one subject.
_WRAPPER_CALL_RE = re.compile(
    r"\.(contains|includes|has|indexOf|startsWith|endsWith|match|like|"
    r"containsString|hasItem)\s*\([^()]*\)\s*$")

# A wrapper call around the whole subject (`str(x)`, `json.dumps(x)`) is just
# a way of stringifying it — the subject underneath is unchanged.
_WRAPPER_FUNC_RE = re.compile(
    r"^(?:str|repr|json\.dumps|dumps|stringify|JSON\.stringify|format)\s*\((?P<inner>.*)\)$",
    re.IGNORECASE)

# Patterns that mean a failure is being hidden rather than fixed.
SWALLOW_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # `except Exception: pass` / `except (A, B): pass` / bare `except: pass`
    ("python-except-pass",
     re.compile(r"except\b[^:\n]*:[ \t]*(?:pass\b|\.\.\.)")),
    ("js-empty-catch",
     re.compile(r"catch\s*(?:\([^()]*\))?\s*\{\s*\}")),
    ("java-empty-catch",
     re.compile(r"catch\s*\([^()]*\)\s*\{\s*\}")),
]

# The same shapes, but with the body on the next line — the common Python
# layout, which a line-by-line scan cannot see.
SWALLOW_MULTILINE: List[Tuple[str, re.Pattern]] = [
    ("python-except-pass",
     re.compile(r"except\b[^:\n]*:[ \t]*\n[ \t]*(?:pass\b|\.\.\.)")),
    ("js-empty-catch",
     re.compile(r"catch\s*(?:\([^()]*\))?\s*\{\s*\}")),
    ("java-empty-catch",
     re.compile(r"catch\s*\([^()]*\)\s*\{\s*\}")),
]

# Patterns that silence a case instead of fixing it.
SKIP_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("pytest-skip", re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)|pytest\.skip\s*\(")),
    ("unittest-skip", re.compile(r"self\.skipTest\s*\(|@unittest\.skip")),
    ("js-skip", re.compile(r"\b(?:it|test|describe)\.skip\b|\bxit\s*\(|\bxdescribe\s*\(")),
    ("go-skip", re.compile(r"\bt\.Skipf?\s*\(")),
    ("junit-ignore", re.compile(r"@(?:Ignore|Disabled)\b")),
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def split_args(text: str) -> List[str]:
    """Split a call argument list on top-level commas."""
    args: List[str] = []
    buf: List[str] = []
    depth = 0
    quote: Optional[str] = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote is not None:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        args.append(tail)
    return args


def is_literal(expr: str) -> bool:
    return bool(_LITERAL_RE.match(expr.strip()))


def _bracket_delta(text: str) -> int:
    """Net call-bracket depth of *text* (open minus close), ignoring strings.

    Only round and square brackets count — braces are deliberately excluded:
    in JS (`it("x", () => {`) and Go (`func TestA(t *testing.T) {`) an
    opening brace stays open across the whole test body, which would glue
    every following assertion into one endless "statement". Call brackets
    balance within a statement, so they are the reliable continuation signal.
    """
    depth = 0
    quote: Optional[str] = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        i += 1
    return depth


def normalize_subject(expr: str) -> str:
    """Normalize a subject expression so equivalent forms compare equal."""
    s = expr.strip()
    # Unwrap `str(x)` / `json.dumps(x)` style wrappers (possibly nested).
    for _ in range(3):
        m = _WRAPPER_FUNC_RE.match(s)
        if not m:
            break
        s = m.group("inner").strip()
    s = _WRAPPER_CALL_RE.sub("", s)
    s = s.rstrip(",;")
    s = re.sub(r"\s+", " ", s)
    s = s.lower()
    return s


def subject_from_args(arg_text: str, spec: tuple) -> str:
    """Pick the subject out of a call's argument list.

    `spec` is either ("last",) — the last argument that is neither a literal
    (the expected value) nor a context argument (`self`, `t`, `msg`, ...) —
    or ("index", n) — the n-th argument, counting from 0.
    """
    args = split_args(arg_text)
    if spec[0] == "index":
        idx = spec[1]
        if idx < len(args):
            return normalize_subject(args[idx])
        return normalize_subject(arg_text)
    for a in reversed(args):
        if a.lower() in _CTX_ARGS:
            continue
        if is_literal(a):
            continue
        return normalize_subject(a)
    # Everything was a literal — fall back to the whole call so at least
    # identical calls match each other.
    return normalize_subject(arg_text)


def classify(line: str) -> Optional[Tuple[str, str, str]]:
    """Classify one line.

    Returns (strength, subject, matched_form) or None when the line holds no
    assertion.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("//"):
        return None

    for regex, strength, spec in FORMS:
        m = regex.search(line)
        if not m:
            continue

        final_strength = strength
        subject: Optional[str] = None

        if spec[0] == "group":
            subject = normalize_subject(m.group(spec[1]))
        else:
            # `spec` is ("args", "last") or ("args", "index", n); the
            # argument-extraction helper expects the part after "args".
            subject = subject_from_args(m.group("args"), spec[1:])

        # `assertTrue(a == b)` is an equality check wearing a truthy hat.
        if regex is _PY_TRUTHY:
            args = split_args(m.group("args"))
            expr = args[0] if args else ""
            if re.search(r"==|!=", expr):
                final_strength = STRONG
                parts = re.split(r"==|!=", expr, maxsplit=1)
                subject = normalize_subject(parts[0])
        if regex is _JUNIT_WEAK and m.group("fn") == "assertThat":
            # `assertThat(x).isEqualTo(y)` is an equality check.
            if re.search(r"\.isEqualTo\s*\(", line):
                final_strength = STRONG
        if regex is _GO_IF_WEAK:
            # `if a == b { t.Errorf(...) }` asserts the *opposite* of the
            # comparison, so an unequal result fails the test — treat the
            # left-hand side as the subject either way.
            subject = normalize_subject(m.group("left"))

        return final_strength, subject, m.groupdict().get("fn") or regex.pattern[:40]
    return None


def field_like(subject: str) -> bool:
    """True when the subject addresses a specific field / element."""
    return bool(re.search(r"\[\s*['\"]|\.\w+\s*\(|\[\s*\d+\s*\]", subject))


# ---------------------------------------------------------------------------
# diff parsing
# ---------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class DiffLine:
    path: str
    sign: str          # '+' | '-' | ' ' (context)
    old_no: Optional[int]
    new_no: Optional[int]
    text: str
    hunk: int = 0      # hunk index within the file


def parse_unified_diff(diff_text: str) -> List[DiffLine]:
    """Parse a unified diff into per-file added/removed lines.

    Context lines are kept too (sign " "): a call written across several
    lines is often closed by a bracket on a line the change never touched,
    and without those lines the call cannot be balanced.
    """
    out: List[DiffLine] = []
    path = ""
    old_no = 0
    new_no = 0
    hunk_no = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git"):
            path = ""
            continue
        if raw.startswith("--- "):
            continue
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                path = ""
            else:
                path = target[2:] if target.startswith("b/") else target
            continue
        m = _HUNK_RE.match(raw)
        if m:
            old_no = int(m.group(1))
            new_no = int(m.group(3))
            hunk_no += 1
            continue
        if not path:
            continue
        if raw.startswith("+"):
            out.append(DiffLine(path, "+", None, new_no, raw[1:], hunk_no))
            new_no += 1
        elif raw.startswith("-"):
            out.append(DiffLine(path, "-", old_no, None, raw[1:], hunk_no))
            old_no += 1
        elif raw.startswith(" "):
            out.append(DiffLine(path, " ", old_no, new_no, raw[1:], hunk_no))
            old_no += 1
            new_no += 1
        elif raw.startswith("\\"):  # "\ No newline at end of file"
            continue
    return out


def _git_root(path: str) -> Optional[str]:
    """Return the git repository root containing *path*, or None."""
    p = path if os.path.isdir(path) else (os.path.dirname(os.path.abspath(path)) or ".")
    root = os.path.abspath(p)
    while True:
        # `.git` is a directory in a normal clone but a *file* in a worktree
        # or a submodule; testing for existence covers both.
        if os.path.exists(os.path.join(root, ".git")):
            return root
        parent = os.path.dirname(root)
        if parent == root:
            return None
        root = parent


def git_diff(base: str, paths: Sequence[str]) -> str:
    """Produce a unified diff of *paths* against *base*.

    Each path is resolved against its own repository root, so the command
    works no matter which directory it is invoked from and still handles a
    path that lives in a nested repository.
    """
    chunks: List[str] = []
    for path in paths:
        root = _git_root(path)
        if root is None:
            print(f"（--diff 跳过 {path}：不在 git 仓库内）")
            continue
        rel = os.path.relpath(os.path.abspath(path), root)
        # `-U3` keeps a few context lines so that a call written across
        # several lines can be closed by the bracket that follows it.
        cmd = ["git", "-C", root, "diff", "-U3", "--no-color", base]
        # `rel` is always a valid pathspec relative to `root`; "." means the
        # whole tree. Passing an empty string here is rejected by git.
        cmd += ["--", rel]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"git diff 失败：{exc}") from exc
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "git diff 失败")
        chunks.append(proc.stdout)
    return "".join(chunks)


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    path: str
    line: Optional[int]
    kind: str          # 断言降级 / 强断言消失 / 字段断言消失 / 异常被吞 / 用例被跳过 / 仅弱断言
    severity: str      # 高 / 中 / 低
    detail: str
    evidence: List[str] = field(default_factory=list)


# A line that mentions one of these is likely an assertion, and only such a
# line may open a multi-line merge. Without this gate, a non-assertion line
# that leaves a bracket open (a JS `describe(... {`, a Go `func ... {` before
# braces were excluded from counting, a Python `with open(...) as f:`) would
# start a buffer and swallow every following line into one logical statement.
_ASSERT_HINT_RE = re.compile(r"assert|expect|require|should|verify", re.IGNORECASE)


def _merge_view(lines: Sequence[DiffLine]) -> List[DiffLine]:
    """Merge consecutive lines of one file version into logical statements.

    A call written across several lines (`assertEqual(` on one line, the
    arguments on the next) matches no single-line pattern, so a line-based
    scan sees no assertion at all. Joining the continuation lines onto the
    opening line restores the whole call.

    A merge only *starts* on a line that looks like an assertion (see
    `_ASSERT_HINT_RE`); any other line is passed through as-is, even when it
    leaves brackets open. Bracket depth decides where a merged statement
    ends, so unrelated neighbouring statements stay separate.
    """
    out: List[DiffLine] = []
    buf: Optional[DiffLine] = None
    depth = 0

    for dl in lines:
        if buf is None:
            if not _ASSERT_HINT_RE.search(dl.text):
                out.append(dl)
                continue
            buf = dl
            depth = _bracket_delta(dl.text)
            if depth <= 0:
                out.append(buf)
                buf = None
            continue

        merged = buf.text.rstrip() + " " + dl.text.strip()
        buf = DiffLine(buf.path, buf.sign, buf.old_no,
                       dl.new_no if buf.sign == "+" else buf.new_no, merged,
                       buf.hunk)
        depth += _bracket_delta(dl.text)
        if depth <= 0:
            out.append(buf)
            buf = None

    if buf is not None:
        out.append(buf)
    return out


def _rebuild_views(dlines: Sequence[DiffLine]) -> Tuple[List[DiffLine], List[DiffLine]]:
    """Split one file's diff lines into an old-version and a new-version view.

    Each hunk is handled on its own. The old view is made of the context
    lines plus the removed lines; the new view of the context lines plus the
    added lines. Rebuilding both views lets a call that spans several lines
    be closed by a bracket sitting on a line the change never touched — the
    context lines are what makes that possible.

    Only lines that belong to the hunk are used, so a call is never closed
    by an unrelated statement further down the file.
    """
    old_view: List[DiffLine] = []
    new_view: List[DiffLine] = []

    by_hunk: Dict[int, List[DiffLine]] = {}
    for dl in dlines:
        by_hunk.setdefault(dl.hunk, []).append(dl)

    for hunk_lines in by_hunk.values():
        for dl in hunk_lines:
            if dl.sign == "-":
                old_view.append(dl)
            elif dl.sign == "+":
                new_view.append(dl)
            else:  # context — present in both versions
                old_view.append(dl)
                new_view.append(dl)

    return _merge_view(old_view), _merge_view(new_view)


def analyze_diff(lines: Sequence[DiffLine]) -> List[Finding]:
    findings: List[Finding] = []
    by_file: Dict[str, List[DiffLine]] = {}
    for dl in lines:
        by_file.setdefault(dl.path, []).append(dl)

    for path, dlines in by_file.items():
        removed: List[Tuple[Optional[int], str, str]] = []   # (line, subject, text)
        added: List[Tuple[Optional[int], str, str, str]] = []  # (line, strength, subject, text)

        old_view, new_view = _rebuild_views(dlines)

        for dl in old_view:
            if dl.sign != "-":
                continue  # context lines only close a call, they are not edits
            info = classify(dl.text)
            if info is None:
                continue
            _strength, subject, _form = info
            removed.append((dl.old_no, subject, dl.text.strip()))

        for dl in new_view:
            if dl.sign != "+":
                continue
            info = classify(dl.text)
            if info is None:
                continue
            strength, subject, _form = info
            added.append((dl.new_no, strength, subject, dl.text.strip()))

        added_strong = {s for (_l, st, s, _t) in added if st == STRONG}
        added_weak_by_subject: Dict[str, Tuple[Optional[int], str]] = {}
        for (line_no, strength, subject, text) in added:
            if strength == WEAK and subject not in added_weak_by_subject:
                added_weak_by_subject[subject] = (line_no, text)

        for (line_no, subject, text) in removed:
            if subject in added_strong:
                continue  # equivalent strong assertion still present
            if subject in added_weak_by_subject:
                w_line, w_text = added_weak_by_subject[subject]
                findings.append(Finding(
                    path=path, line=line_no, kind="断言降级", severity="高",
                    detail="同一断言主体上，精确比较被替换为更弱的检查",
                    evidence=[f"移除：{text}", f"新增：{w_text}",
                              f"断言主体：{subject}"]))
                continue
            if field_like(subject):
                findings.append(Finding(
                    path=path, line=line_no, kind="字段断言消失", severity="高",
                    detail="针对具体字段的强断言被移除，且未见同主体的强断言补回",
                    evidence=[f"移除：{text}", f"断言主体：{subject}"]))
            else:
                findings.append(Finding(
                    path=path, line=line_no, kind="强断言消失", severity="中",
                    detail="强断言被移除且未见等价强断言补回（可能是合理重构，需复核）",
                    evidence=[f"移除：{text}", f"断言主体：{subject}"]))

        # silent failures / skipped cases in the added lines
        seen: set = set()
        for dl in dlines:
            if dl.sign != "+":
                continue
            for label, pat in SWALLOW_PATTERNS:
                if pat.search(dl.text):
                    key = (dl.new_no, "异常被吞")
                    if key not in seen:
                        seen.add(key)
                        findings.append(Finding(
                            path=path, line=dl.new_no, kind="异常被吞", severity="高",
                            detail="新增代码捕获异常后静默通过，失败被隐藏而非暴露",
                            evidence=[f"{label}：{dl.text.strip()}"]))
                    break
            else:
                for label, pat in SKIP_PATTERNS:
                    if pat.search(dl.text):
                        key = (dl.new_no, "用例被跳过")
                        if key not in seen:
                            seen.add(key)
                            findings.append(Finding(
                                path=path, line=dl.new_no, kind="用例被跳过", severity="低",
                                detail="新增跳过 / 豁免标记，需说明理由并记录",
                                evidence=[f"{label}：{dl.text.strip()}"]))
                        break

        # multi-line swallow blocks (`except ...:` / `pass` on separate lines)
        added_lines = {dl.new_no for dl in dlines if dl.sign == "+" and dl.new_no}
        for f in scan_swallow_blocks(path, added_lines):
            key = (f.line, f.kind)
            if key not in seen:
                seen.add(key)
                findings.append(f)
    return findings


def scan_swallow_blocks(path: str, changed_lines: Optional[set]) -> List[Finding]:
    """Find swallowed exceptions whose body sits on the next line.

    *changed_lines* limits the report to blocks overlapping a changed line
    (diff mode); pass None to report every block in the file (scan mode).
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return []

    findings: List[Finding] = []
    for label, pat in SWALLOW_MULTILINE:
        for m in pat.finditer(content):
            start_line = content.count("\n", 0, m.start()) + 1
            end_line = content.count("\n", 0, m.end()) + 1
            if changed_lines is not None:
                span = set(range(start_line, end_line + 1))
                if not (span & changed_lines):
                    continue
            snippet = " ".join(m.group(0).split())
            findings.append(Finding(
                path=path, line=start_line, kind="异常被吞", severity="高",
                detail="捕获异常后静默通过，失败被隐藏而非暴露",
                evidence=[f"{label}：{snippet[:160]}"]))
    return findings


def analyze_files(paths: Sequence[str]) -> List[Finding]:
    findings: List[Finding] = []
    for root in paths:
        if os.path.isfile(root):
            files = [root]
        else:
            files = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for fn in filenames:
                    files.append(os.path.join(dirpath, fn))
        for path in sorted(files):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            strong = 0
            weak = 0
            for line_no, raw in enumerate(content.splitlines(), start=1):
                # Swallow / skip markers are not assertions, so they are
                # checked independently of the assertion classification.
                swallowed = False
                for label, pat in SWALLOW_PATTERNS:
                    if pat.search(raw):
                        findings.append(Finding(
                            path=path, line=line_no, kind="异常被吞", severity="高",
                            detail="捕获异常后静默通过，失败被隐藏而非暴露",
                            evidence=[f"{label}：{raw.strip()}"]))
                        swallowed = True
                        break
                if not swallowed:
                    for label, pat in SKIP_PATTERNS:
                        if pat.search(raw):
                            findings.append(Finding(
                                path=path, line=line_no, kind="用例被跳过", severity="低",
                                detail="跳过 / 豁免标记，需说明理由并记录",
                                evidence=[f"{label}：{raw.strip()}"]))
                            break

                info = classify(raw)
                if info is None:
                    continue
                if info[0] == STRONG:
                    strong += 1
                else:
                    weak += 1
            findings.extend(scan_swallow_blocks(path, None))
            if weak and not strong:
                findings.append(Finding(
                    path=path, line=None, kind="仅弱断言", severity="中",
                    detail="该文件只有弱断言（无精确相等 / 精确比较类断言）",
                    evidence=[f"强断言 {strong} 条，弱断言 {weak} 条"]))
    return findings


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", default=["."],
                        help="files or directories to scan")
    parser.add_argument("--diff", nargs="?", const="HEAD", default=None, metavar="BASE",
                        help="compare against `git diff BASE` (default BASE=HEAD)")
    parser.add_argument("--diff-file", default=None, metavar="PATCH",
                        help="analyze a unified diff file instead of invoking git")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    paths = args.paths or ["."]
    findings: List[Finding] = []

    if args.diff_file:
        try:
            with open(args.diff_file, "r", encoding="utf-8", errors="replace") as f:
                diff_text = f.read()
        except OSError as exc:
            print(f"无法读取 diff 文件：{exc}", file=sys.stderr)
            return 2
        findings = analyze_diff(parse_unified_diff(diff_text))
    elif args.diff is not None:
        try:
            diff_text = git_diff(args.diff, paths)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            print(f"git diff 失败：{exc}", file=sys.stderr)
            return 2
        findings = analyze_diff(parse_unified_diff(diff_text))
    else:
        findings = analyze_files(paths)

    if args.json:
        print(json.dumps([{
            "path": f.path, "line": f.line, "kind": f.kind,
            "severity": f.severity, "detail": f.detail, "evidence": f.evidence,
        } for f in findings], ensure_ascii=False, indent=2))
        return 1 if findings else 0

    if not findings:
        print("✓ 未发现断言强度回退候选。")
        return 0

    order = {"高": 0, "中": 1, "低": 2}
    findings.sort(key=lambda f: (order.get(f.severity, 3), f.path, f.line or 0))
    print(f"发现 {len(findings)} 处候选（需人工复核，脚本不自动修改）：\n")
    for f in findings:
        loc = f"{f.path}:{f.line}" if f.line else f.path
        print(f"{loc} | [{f.severity}] {f.kind}")
        print(f"    {f.detail}")
        for e in f.evidence:
            print(f"    - {e}")
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
