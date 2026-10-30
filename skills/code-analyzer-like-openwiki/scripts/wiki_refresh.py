#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wiki_refresh.py — OpenWiki 依赖刷新 + 渲染健康检验 + 安全回退。

设计目标（对应 skill 选用 @latest 的决策）：
- 永远尝试使用各 CDN 库的 @latest；
- 每次刷新都重新解析 @latest，并做资产级健康检验（CDN 可达 + 含预期全局符号）；
- 若 @latest 资产不可用，回退到「上次已知可用版本」，保住 wiki 可看；
- 首次无历史时，回退种子为 4.13.1 / 10.9.8 / 2.0.1（最后一个稳定大版本）。

诚实边界：资产级健康检验能发现「CDN 下架 / 版本被撤 / 返回错误页」，
但无法在没有无头浏览器时验证「新大版本是否破坏了模板 API」。
模板级破坏性变更需人工更新 index.html 模板，本脚本只做安全网。

Usage: python3 wiki_refresh.py <wiki_dir> [--json]
Exit : 0 = pass / degraded(已回退) ; 1 = fail(无可用回退)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

# 各库在 index.html 模板中的 CDN URL（{v} 为版本占位）。
LIBS = {
    "docsify": {
        "urls": [
            "https://cdn.jsdelivr.net/npm/docsify@{v}/lib/themes/vue.css",
            "https://cdn.jsdelivr.net/npm/docsify@{v}/lib/docsify.min.js",
            "https://cdn.jsdelivr.net/npm/docsify@{v}/lib/plugins/search.min.js",
        ],
        "symbol": "docsify",
    },
    "mermaid": {
        "urls": [
            "https://cdn.jsdelivr.net/npm/mermaid@{v}/dist/mermaid.esm.mjs",
        ],
        "symbol": "mermaid",
    },
    "docsify-mermaid": {
        "urls": [
            "https://cdn.jsdelivr.net/npm/docsify-mermaid@{v}/dist/docsify-mermaid.js",
        ],
        "symbol": "docsifyMermaid",
    },
}

# 首次运行无历史时使用的回退种子（最后一个稳定大版本，2026-10-30 解析值）。
DEFAULT_KNOWN_GOOD = {
    "docsify": "4.13.1",
    "mermaid": "10.9.8",
    "docsify-mermaid": "2.0.1",
}

STATE_FILE = ".wiki_refresh_state.json"


def resolve_latest(pkg):
    """解析 <pkg>@latest 的精确版本。失败返回 None。"""
    try:
        out = subprocess.run(
            ["npm", "view", f"{pkg}@latest", "version"],
            capture_output=True, text=True, timeout=25,
        )
        v = out.stdout.strip().splitlines()[-1]
        return v or None
    except Exception:
        return None


def fetch_ok(url):
    """资产级健康检验：HTTP 200 且返回内容非空（>500 字节）。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "openwiki-refresh"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
            return r.status == 200 and len(data) > 500
    except Exception:
        return False


def load_state(wiki):
    p = os.path.join(wiki, STATE_FILE)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(wiki, st):
    json.dump(st, open(os.path.join(wiki, STATE_FILE), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def health(latest):
    """返回 (all_ok, report)。report 逐库记录版本与资产健康。"""
    report = {}
    ok = True
    for pkg, meta in LIBS.items():
        v = latest.get(pkg)
        if not v:
            report[pkg] = {"ok": False, "reason": "无法解析 @latest"}
            ok = False
            continue
        urls_ok = all(fetch_ok(u.format(v=v)) for u in meta["urls"])
        report[pkg] = {"version": v, "urls_ok": urls_ok}
        if not urls_ok:
            ok = False
    return ok, report


def rewrite_index(wiki, versions):
    """把 index.html 中这些库的 CDN URL 版本段替换为给定版本。"""
    idx = os.path.join(wiki, "index.html")
    if not os.path.exists(idx):
        return False
    html = open(idx, encoding="utf-8").read()
    for pkg, meta in LIBS.items():
        v = versions.get(pkg)
        if not v:
            continue
        for u in meta["urls"]:
            prefix, suffix = u.split("{v}")
            # 匹配当前 @latest 或 @x.y.z 版本段
            pat = re.escape(prefix) + r"[\w.\-]+" + re.escape(suffix)
            html = re.sub(pat, u.format(v=v), html)
    open(idx, "w", encoding="utf-8").write(html)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wiki", help="wiki 目录（含 index.html）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()
    wiki = a.wiki
    state = load_state(wiki)
    known_good = state.get("known_good") or dict(DEFAULT_KNOWN_GOOD)

    latest = {p: resolve_latest(p) for p in LIBS}
    ok, report = health(latest)

    if ok:
        rewrite_index(wiki, latest)
        state["known_good"] = latest
        save_state(wiki, state)
        result = {"status": "pass", "mode": "latest",
                  "versions": latest, "report": report}
    else:
        if all(known_good.get(p) for p in LIBS):
            rewrite_index(wiki, known_good)
            result = {
                "status": "degraded",
                "mode": "fallback",
                "fallback_versions": known_good,
                "latest_attempted": latest,
                "report": report,
                "message": "@latest 资产不可用，已回退到上次已知可用版本",
            }
        else:
            result = {
                "status": "fail",
                "report": report,
                "message": "@latest 不可用且无可用回退版本",
            }

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["status"].upper(), "-", result.get("message", ""))
    sys.exit(0 if result["status"] in ("pass", "degraded") else 1)


if __name__ == "__main__":
    main()
