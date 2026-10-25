"""localcore.net 自测：自带假上游，不依赖任何项目。"""

import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# localcore/tests/test_net.py -> localcore -> 工作区根
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from localcore import net  # noqa: E402


def dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_fake(responder):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            status, payload = responder(self.path)
            body = (payload if isinstance(payload, (bytes, str))
                    else json.dumps(payload, ensure_ascii=False)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class TestNet(unittest.TestCase):
    def test_fetch_roundtrip_and_404(self):
        srv, port = make_fake(lambda p: (200, {"ok": 1}) if p == "/x" else (404, {}))
        try:
            self.assertEqual(net.fetch(port, "/x").status, 200)
            self.assertEqual(net.fetch(port, "/nope").status, 404)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_fetch_dead_port(self):
        with self.assertRaises(net.UpstreamError):
            net.fetch(dead_port(), "/")

    def test_probe_online_offline(self):
        srv, port = make_fake(lambda p: (200, "ok"))
        try:
            self.assertTrue(net.probe(port)[0])
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertFalse(net.probe(dead_port(), timeout=0.3)[0])

    def test_path_allowed(self):
        yes = ("/api", "/thumb")
        self.assertTrue(net.path_allowed("/api/x", yes))
        self.assertFalse(net.path_allowed("/apix", yes))
        self.assertFalse(net.path_allowed("api", yes))


if __name__ == "__main__":
    unittest.main()
