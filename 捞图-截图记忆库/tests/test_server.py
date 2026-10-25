"""server 测试：临时库 + 假 OCR，起临时端口，用 http.client 全路由覆盖。

注意（工作区教训）：cmd 下不用 urlopen 动态 URL；HTTPConnection 不支持 with；
中文查询参数需 quote。
"""

import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from PIL import Image

from laotu.db import Database
from laotu.indexer import Indexer
from laotu.server import ScanState, make_server
from laotu.web import WEB_DIR


def fake_ocr(path):
    return ("红色 优惠券 单号2026", "")


def fake_thumb(path, dest, max_side=360):
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8)).save(dest, "JPEG")
    return (8, 8)


def fake_dims(path):
    return (8, 8)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "shots"
        self.root.mkdir()
        Image.new("RGB", (16, 16), "red").save(self.root / "a.png")
        Image.new("RGB", (16, 16), "blue").save(self.root / "b.png")

        self.db = Database(Path(self.tmp.name) / "t.db")
        self.idx = Indexer(self.db, ocr_func=fake_ocr, thumb_func=fake_thumb,
                           dim_func=fake_dims, thumb_dir=Path(self.tmp.name) / "thumbs")
        self.state = ScanState()
        self.idx.scan([str(self.root)])

        self.server = make_server(0, self.db, self.idx, self.state, WEB_DIR)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.db.close()
        self.tmp.cleanup()

    def _get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def _post(self, path: str, obj: dict):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, body=body,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    # ---------- 页面 ----------

    def test_index_page(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("捞图".encode("utf-8"), body)

    def test_static_404(self):
        status, _ = self._get("/no/such/route")
        self.assertEqual(status, 404)

    # ---------- 搜索 API ----------

    def test_search_fts_chinese(self):
        q = urllib.parse.quote("优惠券")
        status, body = self._get(f"/api/search?q={q}")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["mode"], "fts")
        self.assertEqual(data["total"], 2)
        self.assertTrue(all("snippet" in it for it in data["items"]))

    def test_search_recent_when_empty_q(self):
        status, body = self._get("/api/search")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["mode"], "recent")
        self.assertEqual(data["total"], 2)

    def test_search_like_fallback(self):
        q = urllib.parse.quote("截")
        _, body = self._get(f"/api/search?q={q}")
        data = json.loads(body.decode("utf-8"))
        self.assertIn(data["mode"], ("like", "fts"))

    # ---------- 资源 ----------

    def test_thumb_served(self):
        status, body = self._get("/thumb/1")
        self.assertEqual(status, 200)
        self.assertEqual(body[:2], b"\xff\xd8")  # JPEG magic

    def test_thumb_bad_id_404(self):
        status, _ = self._get("/thumb/99999")
        self.assertEqual(status, 404)

    def test_img_served_original(self):
        status, body = self._get("/img/1")
        self.assertEqual(status, 200)
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")  # PNG magic

    # ---------- stats ----------

    def test_stats(self):
        _, body = self._get("/api/stats")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["total"], 2)
        self.assertIn("indexing", data)
        self.assertIn("roots", data)

    # ---------- POST ----------

    def test_open_with_mock_startfile(self):
        with mock.patch("laotu.server.os.startfile") as sf:
            status, body = self._post("/api/open", {"id": 1})
            data = json.loads(body.decode("utf-8"))
            self.assertTrue(data["ok"])
            sf.assert_called_once()

    def test_open_bad_id(self):
        status, body = self._post("/api/open", {"id": 424242})
        self.assertEqual(status, 404)

    def test_add_dir_invalid(self):
        status, body = self._post("/api/dirs", {"path": r"Z:\ surely\nope"})
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data["ok"])

    def test_add_dir_ok_and_scanned(self):
        extra = Path(self.tmp.name) / "extra"
        extra.mkdir()
        Image.new("RGB", (16, 16), "green").save(extra / "c.png")
        status, body = self._post("/api/dirs", {"path": str(extra)})
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["ok"])
        # 后台线程扫描完成后应能搜到
        import time
        for _ in range(50):
            q = urllib.parse.quote("单号")
            _, b = self._get(f"/api/search?q={q}")
            if json.loads(b.decode("utf-8"))["total"] >= 3:
                break
            time.sleep(0.1)
        _, b = self._get(f"/api/search?q={q}")
        self.assertGreaterEqual(json.loads(b.decode("utf-8"))["total"], 3)

    def test_rescan_triggered(self):
        status, body = self._post("/api/rescan", {})
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["ok"])
        import time
        deadline = time.time() + 5
        while time.time() < deadline:
            _, b = self._get("/api/stats")
            snap = json.loads(b.decode("utf-8"))["indexing"]
            if not snap["active"] and snap["last_summary"]:
                break
            time.sleep(0.1)
        self.assertIsNotNone(snap.get("last_summary"))


if __name__ == "__main__":
    unittest.main()
