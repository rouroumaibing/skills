#!/usr/bin/env python3
"""Report hardcoded-value candidates in delivered test code.

Eight-item reconciliation ("八对账") includes 写死值清零: literal values copied
from exploration transcripts (real tokens, order ids, sample response values,
environment addresses / URLs / hostnames) must not survive into delivered
code — they break the moment the environment or the data rotates. This
script is the mechanical check for that item.

It scans code files line by line and reports lines that look like they embed
such literals. It only *reports* — it never edits files. Exit code is 0 when
nothing looks hardcoded, 1 when candidates need human review.

Design notes:
- High-confidence shapes only (宁缺勿滥): tokens, JWTs, API keys, long hex
  or digit runs, UUIDs, CN mobile numbers, full ISO timestamps, private-IP
  ranges, and environment URLs/hostnames (internal TLD like .local/.internal/
  .corp/.lan, bare no-dot hosts, env-suffixed domains like api-dev.*). Plain
  integers, versions like "1.2.3", dates without a time part and public
  domains (api.example.org) do not fire; bare hostnames without a URL
  scheme are left to manual review (too ambiguous to auto-flag).
- Output is a lead, not a conclusion: a legitimate fixed sample value may
  match. Mark the line with an `allow-hardcode` comment to document and
  suppress it; suppression is per-line and visible in review.
- Two modes: whole-directory scan, or `--diff BASE` to inspect only lines
  the working tree added relative to BASE (useful before a commit).

Usage:
    python3 check_hardcoded_values.py <dir> [--json]
    python3 check_hardcoded_values.py --diff <base> [--] [<pathspec>...]
    python3 check_hardcoded_values.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

# Code-ish extensions worth scanning; markdown and html are skipped because
# sample values in docs are expected there, not defects.
CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".java", ".rb", ".php",
    ".sh", ".bash", ".yaml", ".yml", ".json", ".toml", ".ini", ".conf",
    ".sql", ".kt", ".swift", ".rs",
}

# (category, compiled regex). All are line-level, high-confidence shapes.
PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Bearer token", re.compile(r"Bearer\s+[A-Za-z0-9\-_.]{16,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{20,}")),
    ("API key (sk-)", re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    ("长 hex 串", re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{24,}(?![0-9a-fA-F])")),
    ("UUID", re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("ISO 时间戳", re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")),
    ("超长数字 ID", re.compile(r"(?<!\d)\d{16,}(?!\d)")),
    ("内网 IP", re.compile(
        r"(?<![\w.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|169\.254\.\d{1,3}\.\d{1,3}"
        r"|127\.0\.0\.1)(?![\w.])")),
    ("环境 URL/主机名", re.compile(
        r"https?://(?:[a-z0-9-]+\.)*(?:local|internal|corp|lan)(?=$|[\s/\"':?&)])"
        r"|https?://[a-z0-9-]+(?::\d{2,5})?(?=$|[\s/\"':?&)])"
        r"|https?://[a-z0-9.-]*(?:-dev|-test|-staging|-uat|-pre|-prod|-gray)[-a-z0-9.:/]*")),
]

EXEMPT_MARK = "allow-hardcode"


@dataclass
class Hit:
    file: str
    line: int          # 1-based; 0 when unknown (diff context lost)
    category: str
    excerpt: str


def _excerpt(s: str, limit: int = 120) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit] + "..."


def scan_line(line: str) -> Optional[str]:
    """Category of the first pattern the line matches, else None."""
    if EXEMPT_MARK in line:
        return None
    for category, rx in PATTERNS:
        if rx.search(line):
            return category
    return None


def iter_code_files(root: str) -> Iterable[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__")]
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext in CODE_EXTS:
                yield os.path.join(dirpath, fn)


def scan_directory(root: str) -> List[Hit]:
    self_path = os.path.abspath(__file__)
    hits: List[Hit] = []
    for path in iter_code_files(root):
        if os.path.abspath(path) == self_path:
            continue  # the scanner's own selftest fixtures are not defects
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    category = scan_line(line)
                    if category:
                        hits.append(Hit(path, lineno, category, _excerpt(line)))
        except OSError:
            continue
    return hits


def _git(root: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", root, *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git command failed")
    return proc.stdout


def scan_diff(root: str, base: str, pathspec: Sequence[str]) -> List[Hit]:
    """Scan only lines added relative to BASE inside one git work tree."""
    if not pathspec:
        pathspec = ["."]  # empty pathspec would make git exit 128
    out = _git(root, "diff", "-U0", base, "--", *pathspec)
    hits: List[Hit] = []
    current = ""
    lineno = 0
    skip_file = False
    self_path = os.path.abspath(__file__)
    for raw in out.splitlines():
        if raw.startswith("+++ b/"):
            current = os.path.join(root, raw[6:])
            skip_file = (os.path.splitext(current)[1].lower() not in CODE_EXTS
                         or os.path.abspath(current) == self_path)
            lineno = 0
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            if lineno and not skip_file:
                category = scan_line(raw[1:])
                if category:
                    hits.append(Hit(current, lineno, category,
                                    _excerpt(raw[1:])))
            lineno += 1
    return hits


def selftest() -> int:
    should_hit = [
        ('headers = {"Authorization": "Bearer abcdef0123456789abcd"}',
         "Bearer token"),
        ("token = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload'",
         "JWT"),
        ("key = 'sk-abcdef0123456789'", "API key (sk-)"),
        ("trace = '0123456789abcdef012345678'", "长 hex 串"),
        ("uid = '550e8400-e29b-41d4-a716-446655440000'", "UUID"),
        ("phone = '13812345678'", "手机号"),
        ("ts = '2026-11-13T10:30:00'", "ISO 时间戳"),
        ("order = '1234567890123456'", "超长数字 ID"),
        ('base_url = "http://10.1.2.3:8080"', "内网 IP"),
        ('host = "172.16.3.4"', "内网 IP"),
        ('ssh = "root@192.168.1.10"', "内网 IP"),
        ('url = "https://jumpbox.internal:22"', "环境 URL/主机名"),
        ('url = "http://myhost:9090/api"', "环境 URL/主机名"),
        ('url = "https://api-dev.example.com/v1"', "环境 URL/主机名"),
        ('url = "http://svc.cluster.local:9090"', "环境 URL/主机名"),
    ]
    should_miss = [
        "resp = client.get(url, params={'page': 2})",
        "assert resp['code'] == 0",
        "version = '1.2.3'",
        "quadruple = '1.2.3.4'",                  # 4 octets but public range
        "url = 'https://github.com/example/repo'",        # public domain
        "endpoint = 'https://api.example.org/v1'",         # public domain
        "see https://nodejs.example.org/dist/ for details",  # public domain in comment
        "short_hex = '0123456789abcdef'",        # 16 hex: below threshold
        "digits12 = '123456789012'",              # 12 digits: below threshold
        "date_only = '2026-11-13'",               # no time part
        "Bearer abc allow-hardcode",              # exempt mark wins
        "'Bearer shorttok'",                      # token below threshold
    ]
    failures = []
    for line, want in should_hit:
        got = scan_line(line)
        if got != want:
            failures.append(f"expected {want!r}, got {got!r} on: {line}")
    for line in should_miss:
        got = scan_line(line)
        if got is not None:
            failures.append(f"expected no hit, got {got!r} on: {line}")
    if failures:
        print("selftest FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"✓ selftest passed: {len(should_hit)} must-hit, "
          f"{len(should_miss)} must-miss, exemption honored.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", help="directory to scan")
    parser.add_argument("--diff", metavar="BASE",
                        help="scan only lines added relative to BASE")
    parser.add_argument("--selftest", action="store_true",
                        help="run built-in pattern assertions and exit")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    if not args.target:
        parser.error("a target directory is required unless --selftest")

    if args.diff:
        root = os.path.abspath(args.target)
        git_dir = root if os.path.isdir(root) else os.path.dirname(root)
        if not os.path.isdir(git_dir):
            print(f"error: no such directory: {git_dir}", file=sys.stderr)
            return 2
        try:
            toplevel = _git(git_dir, "rev-parse", "--show-toplevel").strip()
            hits = scan_diff(toplevel, args.diff, [root])
        except RuntimeError as exc:
            print(f"git error: {exc}", file=sys.stderr)
            return 2
    else:
        hits = scan_directory(args.target)

    if args.json:
        print(json.dumps([{
            "file": h.file, "line": h.line,
            "category": h.category, "excerpt": h.excerpt,
        } for h in hits], ensure_ascii=False, indent=2))
    else:
        if not hits:
            print("✓ 写死值候选 0 条。")
        else:
            print(f"发现 {len(hits)} 条写死值候选（只报告，不改文件；"
                  f"行内注释 {EXEMPT_MARK} 可豁免并留痕）：\n")
            for h in hits:
                print(f"[{h.category}] {h.file}:{h.line}")
                print(f"    {h.excerpt}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
