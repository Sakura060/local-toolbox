"""HTTP 服务测试：扫描 API → 簇列表 → diff 全链路。"""

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import build_docx, set_mtime

from docline.engine import Database, Engine
from docline.server import ScanState, make_server

T0 = 1_700_000_000.0


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        self.root.mkdir()
        build_docx(self.root / "方案v1.docx", ["项目背景说明文字", "预算总额一百二十万"])
        build_docx(self.root / "方案v2.docx", ["项目背景说明文字",
                                             "预算总额调整为一百三十五万元",
                                             "新增验收标准章节"])
        set_mtime(self.root / "方案v1.docx", T0)
        set_mtime(self.root / "方案v2.docx", T0 + 100)

        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.db = Database(self.data / "t.db")
        self.engine = Engine(self.db)
        self.state = ScanState()
        self.server = make_server(0, self.db, self.engine, self.state)
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
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
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

    def test_scan_then_clusters_then_diff(self):
        status, resp = self._post("/api/scan", {"paths": [str(self.root)]})
        data = json.loads(resp.decode("utf-8"))
        self.assertTrue(data["ok"])

        # 等待后台扫描完成
        deadline = time.time() + 15
        st = None
        while time.time() < deadline:
            _, b = self._get("/api/stats")
            st = json.loads(b.decode("utf-8"))
            if not st["indexing"]["active"]:
                break
            time.sleep(0.2)
        self.assertEqual(st["docs"], 2)

        _, b = self._get("/api/clusters")
        clusters = [c for c in json.loads(b.decode("utf-8"))["clusters"]
                    if c["size"] > 1]
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertIn("方案v2", c["latest_name"])
        self.assertEqual(len(c["members"]), 2)

        ids = {m["name"]: m["id"] for m in c["members"]}
        status, b = self._get(f"/api/diff?a={ids['方案v1.docx']}"
                              f"&b={ids['方案v2.docx']}")
        data = json.loads(b.decode("utf-8"))
        self.assertEqual(data["diff"]["same"], 1)
        self.assertEqual(len(data["diff"]["changed"]), 1)
        self.assertIn("一百三十五万", data["diff"]["changed"][0]["new"])

    def test_stats(self):
        _, b = self._get("/api/stats")
        st = json.loads(b.decode("utf-8"))
        self.assertIn("indexing", st)
        self.assertIn("docs", st)

    def test_doc_404(self):
        status, _ = self._get("/api/doc/99999")
        self.assertEqual(status, 404)

    def test_bad_route(self):
        status, _ = self._get("/api/nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
