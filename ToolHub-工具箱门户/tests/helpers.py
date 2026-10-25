"""测试公共件：假上游 HTTP 服务器 + 端口占位。"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def dead_port() -> int:
    """拿一个当前无人监听的端口（先占后放）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_fake_upstream(responder):
    """responder(path_qs) -> (status:int, payload:dict|str|bytes)。

    启动一个 127.0.0.1 随机端口的假工具服务，返回 (server, port)。
    """

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            status, payload = responder(self.path)
            if isinstance(payload, bytes):
                body = payload
                ctype = "application/octet-stream"
            elif isinstance(payload, str):
                body = payload.encode("utf-8")
                ctype = "text/plain; charset=utf-8"
            else:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                ctype = "application/json; charset=utf-8"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]
