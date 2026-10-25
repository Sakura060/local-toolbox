"""户口本 server 测试：种子数据 + 全路由。"""

import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from hukou.db import Database
from hukou.server import make_server
from hukou.web import WEB_DIR


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.db.upsert_origin(
            r"C:\Users\t\Downloads\setup.exe", "网络下载",
            "https://example.com/setup（Chrome 下载记录）", "ads",
        )
        self.db.upsert_origin(
            r"C:\Users\t\Documents\xwechat_files\wx\msg\file\2026-09\doc.pdf",
            "微信接收", "路径包含 xwechat_files（推测）", "dir",
        )
        self.db.add_events("C", [
            {"usn": 1, "ts": 1767000000.0, "frn": 1, "pfrn": 2,
             "name": "setup.exe", "reason": "4096"},   # 改名(旧名)
            {"usn": 2, "ts": 1767000060.0, "frn": 1, "pfrn": 2,
             "name": "setup.exe", "reason": "8192"},   # 改名(新名)
        ])
        self.server = make_server(0, self.db)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.db.close()
        self.tmp.cleanup()

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def _post(self, path, obj):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request("POST", path, body=json.dumps(obj).encode("utf-8"),
                         headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def test_page(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("户口".encode("utf-8"), body)

    def test_lookup_recent_empty_q(self):
        _, body = self._get("/api/lookup")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["mode"], "recent")
        self.assertEqual(data["total"], 2)

    def test_lookup_search(self):
        q = urllib.parse.quote("setup")
        _, body = self._get(f"/api/lookup?q={q}")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["total"], 1)
        item = data["items"][0]
        self.assertEqual(item["name"], "setup.exe")
        self.assertIn("example.com", item["detail"])
        self.assertEqual(len(item["events"]), 2)
        self.assertEqual(item["events"][0]["reason_names"], ["改名(新名)"])

    def test_lookup_exact_path(self):
        q = urllib.parse.quote(r"C:\Users\t\Downloads\setup.exe")
        _, body = self._get(f"/api/lookup?q={q}")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["exact"], r"C:\Users\t\Downloads\setup.exe")
        self.assertGreaterEqual(data["total"], 1)

    def test_stats(self):
        _, body = self._get("/api/stats")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["origins"], 2)
        self.assertEqual(data["events"], 2)

    def test_post_scan_empty_paths(self):
        # 没有默认目录时也应优雅返回（不抛异常）
        status, body = self._post("/api/scan", {"paths": [self.tmp.name]})
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertIn("files", data["summary"])

    def test_post_bad_route(self):
        status, _ = self._post("/api/nope", {})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
