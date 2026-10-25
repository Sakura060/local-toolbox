"""server：路由/操作端点。store 用临时目录真件，回贴 mock 掉（不碰真实剪贴板）。"""

import http.client
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from clipvault.server import make_server
from clipvault.store import Store, content_hash


def make_png_bytes() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (4, 3), (200, 60, 60)).save(buf, "PNG")
    return buf.getvalue()


def req(port: int, method: str, path: str, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.store = Store(tmp / "vault.db", tmp / "images")
        # 种子：两条文本 + 一个文件 + 一张真 PNG
        cls.t1 = cls.store.capture("text", "运费多少设计防火规范",
                                   chash=content_hash("运费多少设计防火规范"))
        cls.t2 = cls.store.capture("text", "meeting notes",
                                   chash=content_hash("meeting notes"))
        cls.f1 = cls.store.capture("files", r"C:\a\报告.docx",
                                   chash=content_hash("baogao"))
        png = make_png_bytes()
        name = "seed.png"
        (tmp / "images" / name).write_bytes(png)
        cls.i1 = cls.store.capture("image", "", image_path=name,
                                   size=len(png), chash=content_hash(png))
        cls.httpd = make_server(0, cls.store, tmp / "images")
        cls.port = cls.httpd.server_address[1]
        import threading
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.store.close()
        cls._tmp.cleanup()

    def test_index_page(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("ClipVault", body)
        self.assertIn("剪贴板时间线", body)

    def test_list_and_search(self):
        import urllib.parse
        status, data = req(self.port, "GET", "/api/list")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["entries"]), 4)
        q = urllib.parse.quote("运费")
        status, data = req(self.port, "GET", f"/api/list?q={q}")
        self.assertEqual(len(data["entries"]), 1)
        status, data = req(self.port, "GET", "/api/list?kind=image")
        self.assertEqual(len(data["entries"]), 1)
        self.assertIn("KB", data["entries"][0]["preview"])

    def test_image_endpoint(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", f"/api/image/{self.i1['id']}")
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "image/png")
        self.assertGreater(len(body), 50)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/image/99999")
        self.assertEqual(conn.getresponse().status, 404)
        conn.close()

    def test_stats(self):
        status, s = req(self.port, "GET", "/api/stats")
        # 与 /api/list 自洽（前序用例可能删改过数据），且采集未暂停
        status2, data = req(self.port, "GET", "/api/list")
        self.assertEqual(s["total"], len(data["entries"]))
        self.assertEqual(sum(s["by_kind"].values()), s["total"])
        self.assertFalse(s["paused"])

    def test_pin_delete_pause_clear(self):
        status, d = req(self.port, "POST", "/api/pin",
                        {"id": self.t2["id"], "pinned": True})
        self.assertTrue(d["ok"])
        status, data = req(self.port, "GET", "/api/list")
        self.assertEqual(data["entries"][0]["id"], self.t2["id"])  # 置顶排最前
        status, d = req(self.port, "POST", "/api/pin",
                        {"id": self.t2["id"], "pinned": False})
        status, d = req(self.port, "POST", "/api/delete", {"id": self.t2["id"]})
        self.assertTrue(d["ok"])
        status, d = req(self.port, "POST", "/api/pause", {"paused": True})
        self.assertTrue(d["paused"])
        req(self.port, "POST", "/api/pause", {"paused": False})
        status, d = req(self.port, "POST", "/api/clear", {"keep_pinned": True})
        self.assertGreaterEqual(d["deleted"], 2)  # t2 已删，剩 t1/f1/i1

    def test_copy_mocked_no_real_clipboard(self):
        with patch("clipvault.clip.write_text", return_value=True) as m:
            status, d = req(self.port, "POST", "/api/copy", {"id": self.t1["id"]})
        self.assertTrue(d["ok"])
        self.assertEqual(d["what"], "文本")
        m.assert_called_once_with("运费多少设计防火规范")

    def test_copy_image_uses_write_image(self):
        # v0.2：图片条目回贴 = CF_DIB 位图，不再是文件路径文本
        with patch("clipvault.clip.write_image", return_value=True) as m:
            status, d = req(self.port, "POST", "/api/copy", {"id": self.i1["id"]})
        self.assertEqual(status, 200)
        self.assertTrue(d["ok"])
        self.assertEqual(d["what"], "图片")
        (png_bytes,), _ = m.call_args
        self.assertEqual(png_bytes, (self.store.images_dir / "seed.png").read_bytes())

    def test_copy_files_uses_write_files(self):
        # v0.2：文件条目回贴 = CF_HDROP 文件列表
        with patch("clipvault.clip.write_files", return_value=True) as m:
            status, d = req(self.port, "POST", "/api/copy", {"id": self.f1["id"]})
        self.assertEqual(status, 200)
        self.assertTrue(d["ok"])
        self.assertEqual(d["what"], "文件列表")
        (paths,), _ = m.call_args
        self.assertEqual(paths, [r"C:\a\报告.docx"])

    def test_copy_image_missing_file_404(self):
        ghost = self.store.capture("image", "", image_path="ghost.png",
                                   size=1, chash=content_hash("ghost-png"))
        try:
            status, d = req(self.port, "POST", "/api/copy", {"id": ghost["id"]})
            self.assertEqual(status, 404)
            self.assertIn("missing", d["error"])
        finally:
            self.store.delete(ghost["id"])  # 清掉种子，别影响其他测试的计数断言

    def test_bad_requests(self):
        status, _ = req(self.port, "GET", "/nope")
        self.assertEqual(status, 404)
        status, _ = req(self.port, "POST", "/api/pin", {"id": 99999, "pinned": True})
        self.assertEqual(status, 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/copy", body=b"not json",
                     headers={"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_list_limit_garbage_200(self):
        # 曾因 ValueError 未捕获直接断连（无响应）
        status, data = req(self.port, "GET", "/api/list?limit=abc")
        self.assertEqual(status, 200)
        self.assertIn("entries", data)

    def test_pause_pin_strict_bool(self):
        # 字符串真值坑：{"paused": "false"} 必须等于不暂停
        status, d = req(self.port, "POST", "/api/pause", {"paused": "false"})
        self.assertFalse(d["paused"])
        status, d = req(self.port, "POST", "/api/pause", {"paused": "true"})
        self.assertTrue(d["paused"])
        req(self.port, "POST", "/api/pause", {"paused": False})  # 恢复
        status, d = req(self.port, "POST", "/api/pin",
                        {"id": self.t1["id"], "pinned": "1"})
        status, data = req(self.port, "GET", "/api/list")
        pinned_row = next(r for r in data["entries"] if r["id"] == self.t1["id"])
        self.assertEqual(pinned_row["pinned"], 1)
        req(self.port, "POST", "/api/pin", {"id": self.t1["id"], "pinned": False})

    def test_entry_endpoint_full_body(self):
        status, e = req(self.port, "GET", f"/api/entry/{self.t1['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(e["body"], "运费多少设计防火规范")
        status, _ = req(self.port, "GET", "/api/entry/abc")
        self.assertEqual(status, 404)

    def test_boolean_id_rejected_no_int_coercion(self):
        # 反向检修实证：{"id": true} 曾被 SQLite 当 1 误操作真实条目
        status, _ = req(self.port, "POST", "/api/pin",
                        {"id": True, "pinned": True})
        self.assertEqual(status, 404)
        status, _ = req(self.port, "POST", "/api/copy", {"id": True})
        self.assertEqual(status, 404)
        status, _ = req(self.port, "POST", "/api/delete", {"id": True})
        self.assertEqual(status, 404)
        # id=1 的条目必须毫发无损且未被置顶
        status, data = req(self.port, "GET", "/api/list?limit=50")
        row1 = next(r for r in data["entries"] if r["id"] == 1)
        self.assertEqual(row1["pinned"], 0)

    def test_clear_keep_pinned_strict_bool(self):
        # 同款字符串真值坑：keep_pinned="false" 必须真的连置顶一起删
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "v.db", Path(tmp) / "img")
            self.addCleanup(store.close)
            a = store.capture("text", "pin me", chash=content_hash("pin me"))
            store.capture("text", "plain", chash=content_hash("plain"))
            store.pin(a["id"], True)
            httpd = make_server(0, store, Path(tmp) / "img")
            import threading
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                status, d = req(httpd.server_address[1], "POST", "/api/clear",
                                {"keep_pinned": "false"})
                self.assertEqual(status, 200)
                self.assertEqual(store.stats()["total"], 0)  # 置顶也被删
            finally:
                httpd.shutdown()
                httpd.server_close()
                store.close()  # 必须先于 tmp 目录清理（Windows 文件占用）


if __name__ == "__main__":
    unittest.main()
