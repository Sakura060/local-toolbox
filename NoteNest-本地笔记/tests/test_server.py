"""NoteNest HTTP API 集成测试（固定回环主机）。"""

import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notenest.server import make_server

HOST = "127.0.0.1"


def req(port, method, path, body=None):
    # 中文等非 ASCII 字符自动百分号编码
    path = quote(path, safe="/%?=&")
    conn = http.client.HTTPConnection(HOST, port, timeout=10)
    try:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, {"raw": raw}
    finally:
        conn.close()


class ServerTest(unittest.TestCase):
    port = 8896

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="nn-web-"))
        cls.server = make_server(str(cls.root), HOST, cls.port, quiet=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_index_page(self):
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("GET", "/")
            resp = conn.getresponse()
            html = resp.read().decode("utf-8")
        finally:
            conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("NoteNest", html)
        self.assertIn("mdRender", html)

    def test_color_feature_present(self):
        # 页面必须带颜色标注：调色板、渲染规则、工具栏与包裹函数
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("GET", "/")
            html = conn.getresponse().read().decode("utf-8")
        finally:
            conn.close()
        for token in ("PALETTE", "COLOR_MARK", "paintColors", "buildColors", "wrapSelection",
                      "refreshBackdrop", "backdropHtml", "isLocalAsset", "addImages",
                      "filesFromClipboard", "btnPic", "btnPaint", "paintTarget",
                      "wrapPerLine", "keepScroll", "insideMarker"):
            self.assertIn(token, html)

    def test_asset_upload_serve_delete(self):
        status, n = req(self.port, "POST", "/api/notes", {"title": "图", "content": ""})
        self.assertEqual(status, 201)
        nid = n["id"]
        png = b"\x89PNG\r\n\x1a\n" + b"hello-image-bytes"
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("POST", "/api/notes/%s/assets" % nid, body=png,
                         headers={"Content-Type": "image/png"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 201)
            data = json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()
        url = data["url"]
        self.assertTrue(url.startswith("assets/%s/" % nid))
        self.assertEqual(data["markdown"], "![](%s)" % url)

        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("GET", "/" + url)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.getheader("Content-Type"), "image/png")
            self.assertEqual(resp.read(), png)
        finally:
            conn.close()

        # 删笔记 → 图片一并消失
        req(self.port, "DELETE", "/api/notes/%s" % nid)
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("GET", "/" + url)
            self.assertEqual(conn.getresponse().status, 404)
        finally:
            conn.close()

    def test_asset_rejects_non_image(self):
        status, n = req(self.port, "POST", "/api/notes", {"title": "x", "content": ""})
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("POST", "/api/notes/%s/assets" % n["id"], body=b"<html>nope</html>",
                         headers={"Content-Type": "image/png"})
            self.assertEqual(conn.getresponse().status, 400)
        finally:
            conn.close()
        req(self.port, "DELETE", "/api/notes/%s" % n["id"])   # 别污染共享测试目录

    def test_asset_missing_note_404(self):
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("POST", "/api/notes/zzzzzzzz/assets", body=b"\x89PNG\r\n\x1a\nx",
                         headers={"Content-Type": "image/png"})
            self.assertEqual(conn.getresponse().status, 404)
        finally:
            conn.close()

    def test_asset_unknown_path_404(self):
        status, _ = req(self.port, "GET", "/assets/zzzzzzzz/0123456789abcdef.png")
        self.assertEqual(status, 404)
        status, _ = req(self.port, "GET", "/assets/onlyone")
        self.assertEqual(status, 404)

    def test_full_crud_cycle(self):
        # 创建
        status, n = req(self.port, "POST", "/api/notes",
                        {"title": "测试笔记", "content": ""})
        self.assertEqual(status, 201)
        nid = n["id"]
        self.assertEqual(n["title"], "测试笔记")

        # 读取
        status, got = req(self.port, "GET", f"/api/notes/{nid}")
        self.assertEqual(status, 200)
        self.assertTrue(got["content"].startswith("# 测试笔记"))

        # 更新
        status, up = req(self.port, "PUT", f"/api/notes/{nid}",
                         {"content": "# 新标题\n\n新内容"})
        self.assertEqual(status, 200)
        self.assertEqual(up["title"], "新标题")

        # 列表
        status, lst = req(self.port, "GET", "/api/notes")
        self.assertEqual(status, 200)
        self.assertEqual(len(lst["notes"]), 1)
        self.assertEqual(lst["notes"][0]["title"], "新标题")

        # 搜索
        status, found = req(self.port, "GET", "/api/notes?q=新内容")
        self.assertEqual(len(found["notes"]), 1)

        # 删除
        status, out = req(self.port, "DELETE", f"/api/notes/{nid}")
        self.assertEqual(status, 200)
        status, _ = req(self.port, "GET", f"/api/notes/{nid}")
        self.assertEqual(status, 404)

    def test_get_missing_404(self):
        status, data = req(self.port, "GET", "/api/notes/zzzzzzzz")
        self.assertEqual(status, 404)

    def test_invalid_id_404(self):
        status, _ = req(self.port, "GET", "/api/notes/..%2Fetc")
        self.assertEqual(status, 404)

    def test_bad_json_400(self):
        conn = http.client.HTTPConnection(HOST, self.port, timeout=10)
        try:
            conn.request("POST", "/api/notes", body=b"not json",
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 400)
        finally:
            conn.close()

    def test_unknown_route_404(self):
        status, _ = req(self.port, "GET", "/api/unknown")
        self.assertEqual(status, 404)
        status, _ = req(self.port, "POST", "/api/unknown", {})
        self.assertEqual(status, 404)
        status, _ = req(self.port, "PUT", "/api/unknown", {})
        self.assertEqual(status, 404)

    def test_stats(self):
        status, s = req(self.port, "GET", "/api/stats")
        self.assertEqual(status, 200)
        self.assertIn("count", s)
        self.assertIn("directory", s)

    def test_content_size_limit(self):
        huge = "x" * (600 * 1024)
        status, data = req(self.port, "POST", "/api/notes",
                           {"title": "大", "content": huge})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
