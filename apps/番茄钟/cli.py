"""番茄钟 —— ToolHub Desktop 的示范第三方工具。

存在意义：证明「一个文件夹 + 一份 tool.json 就能被桌面版识别」，
不需要改任何核心代码。本身是一个极简但真正可用的专注计时器：
stdlib HTTP 服务 + 单页倒计时（JS 计时，结束用 WebAudio 响铃，全程离线）。
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE_PATH = Path(__file__).resolve().parent / "page.html"


class Handler(BaseHTTPRequestHandler):
    server_version = "Pomodoro/0.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/stats":
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def main():
    p = argparse.ArgumentParser(prog="pomodoro")
    p.add_argument("cmd", nargs="?", default="serve")
    p.add_argument("--port", type=int, default=8661)
    args = p.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Pomodoro serving at http://127.0.0.1:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
