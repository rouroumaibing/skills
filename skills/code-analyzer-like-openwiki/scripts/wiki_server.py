#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wiki_server.py — OpenWiki 本地预览服务器 + /refresh 端点。

运行：在 wiki 目录下 `python3 wiki_server.py [port]`（默认 8000）。
- 静态托管当前目录（index.html / *.md）；
- GET /refresh 调用同目录 wiki_refresh.py，返回 JSON 状态，
  供 index.html 右上角「重新生成」按钮 fetch 使用。

纯标准库实现，不引入任何第三方依赖（守仓库「环境中立」原则）。
"""
import json
import os
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
ROOT = os.path.dirname(os.path.abspath(__file__))


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] == "/refresh":
            self._handle_refresh()
            return
        super().do_GET()

    def _handle_refresh(self):
        try:
            out = subprocess.run(
                [sys.executable, os.path.join(ROOT, "wiki_refresh.py"),
                 ROOT, "--json"],
                capture_output=True, text=True, timeout=90,
            )
            body = (out.stdout or out.stderr).strip()
            try:
                payload = json.loads(body)
            except Exception:
                payload = {"status": "error", "raw": body}
        except Exception as e:  # pragma: no cover
            payload = {"status": "error", "error": str(e)}
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    os.chdir(ROOT)
    print(f"OpenWiki 服务已启动: http://localhost:{PORT}  (GET /refresh 刷新依赖)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
