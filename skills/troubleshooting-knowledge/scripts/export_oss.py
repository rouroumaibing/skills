#!/usr/bin/env python3
"""export_oss.py — 分发前导出（剔除 knowledge/personal/）并校验隔离。

知识库分两层：oss（开源通用，可分发）/ personal（个人项目，不出库）。分发 skill 时
绝不能把 personal/ 带出去。本脚本把整个 skill 复制到导出目录（忽略 knowledge/personal/），
然后在导出物上跑一遍 validate.py，并扫描 knowledge/ 不得出现 `personal/` 字样
（遗漏的指向 / 引用）。

用法
----
* python3 scripts/export_oss.py                        # 默认 src=本 skill，out=<同级>/<名>.oss-export
* python3 scripts/export_oss.py --src <skill 目录> --out <导出目录>

退出：全部通过 exit 0；任一失败 exit 1。
"""
from __future__ import annotations

import os
import re
import sys
import shutil
import subprocess
import argparse


def die(reason: str) -> None:
    sys.stderr.write(f"status=ERROR msg={reason}\n")
    sys.exit(1)


def _ignore_personal(dirpath: str, names: list[str]) -> list[str]:
    """只在 knowledge/ 目录下忽略 personal 子目录。"""
    if os.path.basename(dirpath.rstrip(os.sep)) == "knowledge":
        return [n for n in names if n == "personal"]
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description="导出 oss 分发版（剔除 personal/）并校验")
    ap.add_argument("--src", default=None, help="skill 目录（默认：本脚本上一级）")
    ap.add_argument("--out", default=None, help="导出目录（默认：<src 同级>/<名>.oss-export）")
    args = ap.parse_args()

    src = args.src or os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(src):
        die(f"skill 源目录不存在: {src}")

    name = os.path.basename(src.rstrip("/"))
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(src)), f"{name}.oss-export")

    if os.path.exists(out):
        shutil.rmtree(out)
    shutil.copytree(src, out, ignore=_ignore_personal)

    # 1) 确认 personal/ 确实没被复制
    personal_dir = os.path.join(out, "knowledge", "personal")
    if os.path.exists(personal_dir):
        die(f"导出物仍含 knowledge/personal/（剔除失败）：{personal_dir}")

    # 2) 在导出物上跑 validate（须 OK，无 error）
    validator = os.path.join(out, "scripts", "validate.py")
    if not os.path.isfile(validator):
        die(f"导出物缺少 scripts/validate.py：{validator}")
    r = subprocess.run([sys.executable, validator], cwd=out, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        sys.stdout.write(r.stdout)
        die("导出物 validate 未通过（见上方 error）")

    # 3) 扫描 knowledge/ 不得出现 personal/ 字样（遗漏的指向 / 引用）
    knowledge_dir = os.path.join(out, "knowledge")
    leaks: list[str] = []
    for root, _dirs, files in os.walk(knowledge_dir):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                text = open(p, encoding="utf-8").read()
            except OSError:
                continue
            for m in re.finditer(r"personal/", text):
                rel = os.path.relpath(p, out)
                leaks.append(f"{rel}:{m.start()}")
    if leaks:
        die("导出物 knowledge/ 中仍出现 personal/ 字样（悬空引用）：\n  " + "\n  ".join(leaks))

    print(f"status=OK exported={out} "
          f"（已剔除 knowledge/personal/，validate 通过，无 personal/ 泄漏）")


if __name__ == "__main__":
    main()
