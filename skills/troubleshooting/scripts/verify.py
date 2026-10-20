#!/usr/bin/env python3
"""verify.py — 修复后复验：批量执行断言，逐条输出 PASS/FAIL。

定位：修复-验证闭环（env-debug.md 第三段）的第 4 步。apply.py 执行写操作后，
用本脚本跑一组可执行断言；全部 PASS 才收尾，任一 FAIL -> 按备份回滚后重验。

断言来源（二选一）：
  --file <checks.yaml>                       批量（推荐，可归档进报告验证表）
  --cmd "<script args>" --expect <regex>     单条内联（可再配 --expect-not/--id）

checks.yaml 形如：
  checks:
    - id: bastion-ok
      cmd: connect.py
      expect: BASTION_OK
    - id: no-new-errors
      cmd: logs.py logs order-svc --since 5m --grep ERROR
      expect-not: ERROR
      timeout: 60

规则：
  * cmd 必须以 scripts/ 下的只读脚本名开头（logs.py / connect.py /
    resolve_version.py）；拒绝裸 kubectl/ssh/apply.py/setup_ssh.py
    （Agent 不得直连，写操作不经复验通道，通道规则同 SKILL.md 安全红线）。
  * 脚本退出码必须为 0（ignore_exit: true 可豁免，如需检查非零输出场景）。
  * expect：正则在 stdout 上 search，命中才 PASS；expect-not：命中即 FAIL；
    两者都未给时只查退出码。
  * 输出逐条 PASS/FAIL + summary；全部通过 status=VERIFY_PASS（exit 0），
    任一失败 status=VERIFY_FAIL（exit 1）；断言文件/参数不合法 status=ERROR
    （exit 2）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# 复验只允许跑只读排查脚本；apply.py（写）、setup_ssh.py（配置期写）一律不进此表
ALLOWED_SCRIPTS = ("logs.py", "connect.py", "resolve_version.py")
DEFAULT_TIMEOUT = 120  # seconds per check


class CheckError(Exception):
    """Malformed check definition (bad cmd/regex/flags) — status=ERROR."""


def _load_checks(args) -> list[dict]:
    checks: list[dict] = []
    if args.file:
        path = os.path.expanduser(args.file)
        if not os.path.isfile(path):
            raise CheckError(f"断言文件不存在: {path}")
        with open(path, encoding="utf-8") as fh:
            if path.endswith(".json"):
                data = json.load(fh)
            else:
                try:
                    import yaml
                    data = yaml.safe_load(fh)
                except ImportError:
                    raise CheckError("需要 PyYAML：请运行 `pip install pyyaml`")
                except Exception as exc:  # yaml.YAMLError and friends
                    raise CheckError(f"断言文件解析失败: {exc}")
        if not isinstance(data, dict) or not isinstance(data.get("checks"), list):
            raise CheckError(f"断言文件结构应为 {{checks: [...]}}: {path}")
        checks = data["checks"]
    else:
        if not args.cmd:
            raise CheckError("需要 --file <checks.yaml> 或 --cmd <script args>")
        check = {"cmd": args.cmd, "expect": args.expect, "expect-not": args.expect_not}
        if args.id:
            check["id"] = args.id
        checks = [check]
    for i, c in enumerate(checks):
        if not isinstance(c, dict) or not c.get("cmd"):
            raise CheckError(f"第 {i + 1} 条断言缺少 cmd")
        try:
            _resolve_script(str(c["cmd"]))
        except CheckError as exc:
            raise CheckError(f"第 {i + 1} 条断言: {exc}")
        if not c.get("expect") and not c.get("expect-not"):
            raise CheckError(f"第 {i + 1} 条断言（{c.get('id') or c['cmd']}）"
                             "至少要有 expect 或 expect-not 之一")
        for key in ("expect", "expect-not"):
            if c.get(key):
                try:
                    re.compile(str(c[key]))
                except re.error as exc:
                    raise CheckError(f"第 {i + 1} 条断言 {key} 正则不合法: {exc}")
        timeout = c.get("timeout", DEFAULT_TIMEOUT)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise CheckError(f"第 {i + 1} 条断言 timeout 必须是正数: {timeout}")
    return checks


def _resolve_script(cmd: str) -> tuple[str, list[str]]:
    """cmd -> (script path, args); refuse anything outside ALLOWED_SCRIPTS."""
    try:
        parts = shlex.split(cmd)
    except ValueError as exc:
        raise CheckError(f"cmd 解析失败: {exc}")
    if not parts:
        raise CheckError("cmd 为空")
    script = os.path.basename(parts[0])
    if script not in ALLOWED_SCRIPTS:
        raise CheckError(
            f"cmd 只允许复验 scripts/ 下只读脚本 {', '.join(ALLOWED_SCRIPTS)}，"
            f"拒绝: {parts[0]}（写操作/裸 kubectl/ssh 不经复验通道）")
    return os.path.join(SCRIPTS_DIR, script), parts[1:]


def run_check(check: dict) -> tuple[bool, str]:
    """Run one check; return (passed, reason)."""
    script_path, script_args = _resolve_script(str(check["cmd"]))
    timeout = check.get("timeout", DEFAULT_TIMEOUT)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, script_path, *script_args],
            capture_output=True, text=True, timeout=timeout,
            cwd=os.path.dirname(script_path))
    except subprocess.TimeoutExpired:
        return False, f"超时（>{timeout}s）"
    duration = time.monotonic() - started
    stdout, stderr = proc.stdout or "", proc.stderr or ""
    if proc.returncode != 0 and not check.get("ignore_exit"):
        tail = (stderr.strip() or stdout.strip()).splitlines()
        detail = tail[-1][:160] if tail else f"exit={proc.returncode}"
        return False, f"脚本退出码 {proc.returncode}: {detail}"
    expect = check.get("expect")
    expect_not = check.get("expect-not")
    if expect and not re.search(str(expect), stdout):
        return False, f"stdout 未命中期望 /{expect}/"
    if expect_not and re.search(str(expect_not), stdout):
        return False, f"stdout 命中禁用模式 /{expect_not}/"
    return True, f"{duration:.1f}s"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="verify.py",
        description="修复后复验：批量执行断言，逐条输出 PASS/FAIL。")
    parser.add_argument("--file", default="",
                        help="断言文件（YAML/JSON，结构 {checks: [...]}）")
    parser.add_argument("--cmd", default="",
                        help="单条内联断言的命令（须以 scripts/ 只读脚本名开头）")
    parser.add_argument("--expect", default="",
                        help="期望：该正则在 stdout 命中才 PASS")
    parser.add_argument("--expect-not", dest="expect_not", default="",
                        help="禁用：该正则在 stdout 命中即 FAIL")
    parser.add_argument("--id", default="inline",
                        help="内联断言的 id（默认 inline）")
    args = parser.parse_args()

    try:
        checks = _load_checks(args)
    except CheckError as exc:
        print(f"status=ERROR msg={exc}")
        return 2

    passed = 0
    for check in checks:
        cid = str(check.get("id") or check["cmd"])[:60]
        try:
            ok, reason = run_check(check)
        except CheckError as exc:
            print(f"FAIL id={cid} reason={exc}")
            continue
        if ok:
            passed += 1
            print(f"PASS id={cid} ({reason})")
        else:
            print(f"FAIL id={cid} reason={reason}")
    failed = len(checks) - passed
    print(f"summary: total={len(checks)} pass={passed} fail={failed}")
    if failed:
        print("status=VERIFY_FAIL")
        return 1
    print("status=VERIFY_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
