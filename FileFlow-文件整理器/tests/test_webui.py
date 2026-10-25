"""Web UI 集成测试：起真实 HTTP 服务，走全链路。

测试客户端仅连接固定回环主机（字面量），请求路径必须命中白名单。
"""

import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fileflow import executor, webui

HOST = "127.0.0.1"          # 固定回环主机（字面量）
ALLOWED_PATHS = frozenset({
    "/", "/api/plan", "/api/run", "/api/undo", "/api/logs",
    "/api/dup", "/api/dup-clean", "/api/nothing",
})
ALLOWED_METHODS = frozenset({"GET", "POST"})


def http_raw(port: int, method: str, path: str, body: dict | None = None):
    """发起受控请求，返回 (status, 响应文本)。"""
    if path not in ALLOWED_PATHS:
        raise ValueError(f"测试不允许访问的路径: {path}")
    if method not in ALLOWED_METHODS:
        raise ValueError(f"测试不允许的方法: {method}")
    conn = http.client.HTTPConnection(HOST, port, timeout=10)
    try:
        payload = json.dumps(body) if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8")
    finally:
        conn.close()


def http_request(port: int, method: str, path: str, body: dict | None = None):
    status, text = http_raw(port, method, path, body)
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, {"raw": text}


class WebUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.logs_tmp = Path(tempfile.mkdtemp(prefix="ff-wlogs-"))
        cls._log_backup = executor.LOG_DIR
        executor.LOG_DIR = str(cls.logs_tmp)
        cls.port = 8871
        cls.server = webui._Server((HOST, cls.port), webui._Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        executor.LOG_DIR = cls._log_backup
        shutil.rmtree(cls.logs_tmp, ignore_errors=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ff-web-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel: str, content: str = "x") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _write_bytes(self, rel: str, content: bytes) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def test_index_page(self):
        status, html = http_raw(self.port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("FileFlow", html)
        self.assertIn("btnPlan", html)

    def test_plan_api_roundtrip(self):
        self._write("a.jpg")
        self._write("b.txt")
        status, data = http_request(self.port, "POST", "/api/plan",
                                    {"directory": str(self.root)})
        self.assertEqual(status, 200)
        self.assertEqual(data["file_count"], 2)
        self.assertEqual(data["move_count"], 2)      # jpg→图片, txt→文档(内置规则)
        dsts = {m["src"]: m["dst"] for m in data["moves"]}
        self.assertEqual(dsts["a.jpg"], "图片/a.jpg")
        self.assertEqual(dsts["b.txt"], "文档/b.txt")

    def test_plan_then_run_then_undo_full_cycle(self):
        self._write("pic.jpg")
        status, plan = http_request(self.port, "POST", "/api/plan",
                                    {"directory": str(self.root)})
        self.assertEqual(plan["move_count"], 1)

        status, run = http_request(self.port, "POST", "/api/run",
                                   {"directory": str(self.root)})
        self.assertEqual(status, 200)
        self.assertEqual(run["moved"], 1)
        self.assertTrue((self.root / "图片" / "pic.jpg").exists())

        status, und = http_request(self.port, "POST", "/api/undo", {})
        self.assertEqual(status, 200)
        self.assertEqual(und["restored"], 1)
        self.assertTrue((self.root / "pic.jpg").exists())

    def test_bad_requests(self):
        status, data = http_request(self.port, "POST", "/api/plan", {})
        self.assertEqual(status, 400)
        self.assertIn("directory", data["error"])

        status, data = http_request(self.port, "POST", "/api/plan",
                                    {"directory": str(self.root / "不存在")})
        self.assertEqual(status, 400)

    def test_unknown_route_404(self):
        status, _ = http_request(self.port, "GET", "/api/nothing")
        self.assertEqual(status, 404)
        status, _ = http_request(self.port, "POST", "/api/nothing", {})
        self.assertEqual(status, 404)

    def test_invalid_json_body(self):
        if "/" not in ALLOWED_PATHS:
            raise AssertionError("sanity")
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("POST", "/api/plan", body=b"not json",
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 400)
        finally:
            conn.close()

    def test_logs_endpoint(self):
        status, data = http_request(self.port, "GET", "/api/logs")
        self.assertEqual(status, 200)
        self.assertIsInstance(data["logs"], list)

    def test_dup_endpoints(self):
        blob = b"same" * 30000
        self._write_bytes("a.bin", blob)
        self._write_bytes("sub/b.bin", blob)
        self._write_bytes("c.bin", b"unique")
        status, data = http_request(self.port, "POST", "/api/dup",
                                    {"directory": str(self.root)})
        self.assertEqual(status, 200)
        self.assertEqual(data["group_count"], 1)
        self.assertEqual(data["groups"][0]["count"], 2)
        self.assertGreater(data["wasted_total"], 0)

        status, clean = http_request(self.port, "POST", "/api/dup-clean",
                                     {"directory": str(self.root)})
        self.assertEqual(status, 200)
        self.assertEqual(clean["moved"], 1)
        self.assertEqual(clean["failed"], 0)
        self.assertTrue((self.root / "a.bin").exists())          # 保留最旧
        self.assertFalse((self.root / "sub" / "b.bin").exists())
        self.assertTrue((self.root / ".fileflow-trash" / "duplicates").exists())

        status, data = http_request(self.port, "POST", "/api/dup", {})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
