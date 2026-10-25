"""服务器集成测试：share 下载与 receive 上传全链路（固定回环主机）。"""

import http.client
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from landrop.server import make_receive_server, make_share_server

HOST = "127.0.0.1"     # 固定回环


def req(port, method, path, body=None, headers=None):
    # 拦原始穿越写法；URL 编码穿越（%2F）是真实攻击形态，允许发出以验证服务端防线
    if "../" in path or "..\\" in path:
        raise ValueError("测试不允许原始目录穿越路径")
    path = quote(path, safe="/%?=&")   # 中文等非 ASCII 自动编码，已有 %XX 保留
    conn = http.client.HTTPConnection(HOST, port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, bytes(resp.read()), dict(resp.getheaders())
    finally:
        conn.close()


class ShareServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        cls.root = Path(tempfile.mkdtemp(prefix="lr-share-"))
        (cls.root / "报告.txt").write_text("内容内容", encoding="utf-8")
        sub = cls.root / "子目录"
        sub.mkdir()
        (sub / "nested.bin").write_bytes(b"\x00\x01nested")
        (cls.root / "data.bin").write_bytes(b"B" * 5000)
        cls.token = "abc123"
        cls.port = 8895   # 8890 是 NoteNest 的常驻端口，避开冲突
        cls.server = make_share_server(str(cls.root), token=cls.token,
                                       host=HOST, port=cls.port, quiet=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        import shutil
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_listing_with_token(self):
        status, body, _ = req(self.port, "GET", f"/s/{self.token}")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("报告.txt", html)
        self.assertIn("data.bin", html)
        self.assertIn("子目录", html)

    def test_wrong_token_403(self):
        status, _, _ = req(self.port, "GET", "/s/wrongtoken")
        self.assertEqual(status, 403)

    def test_download_file(self):
        status, body, headers = req(self.port, "GET", f"/s/{self.token}/data.bin")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 5000)
        self.assertEqual(body, b"B" * 5000)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))

    def test_download_chinese_filename(self):
        status, body, headers = req(self.port, "GET", f"/s/{self.token}/报告.txt")
        self.assertEqual(status, 200)
        self.assertEqual(body.decode("utf-8"), "内容内容")

    def test_subdirectory_navigation(self):
        status, body, _ = req(self.port, "GET", f"/s/{self.token}/子目录")
        self.assertEqual(status, 200)
        self.assertIn("nested.bin", body.decode("utf-8"))
        status, body, _ = req(self.port, "GET", f"/s/{self.token}/子目录/nested.bin")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"\x00\x01nested")

    def test_traversal_blocked(self):
        status, _, _ = req(self.port, "GET",
                           f"/s/{self.token}/..%2F..%2Fwindows%2Fwin.ini")
        self.assertIn(status, (403, 404))   # resolve 后不在 root 内 → 拒绝

    def test_unknown_file_404(self):
        status, _, _ = req(self.port, "GET", f"/s/{self.token}/不存在.txt")
        self.assertEqual(status, 404)


class ReceiveServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        cls.out = Path(tempfile.mkdtemp(prefix="lr-rx-"))
        cls.token = "pin99"
        cls.port = 8891
        cls.server = make_receive_server(str(cls.out), token=cls.token,
                                         host=HOST, port=cls.port, quiet=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        import shutil
        shutil.rmtree(cls.out, ignore_errors=True)

    def _boundary(self):
        return b"----LandropTestBoundary1234"

    def _upload_body(self, files):
        B = self._boundary()
        out = b""
        for name, data in files:
            out += b"--" + B + b"\r\n"
            out += f'Content-Disposition: form-data; name="files"; filename="{name}"\r\n'.encode()
            out += b"Content-Type: application/octet-stream\r\n\r\n"
            out += data + b"\r\n"
        out += b"--" + B + b"--\r\n"
        return out, f"multipart/form-data; boundary={B.decode()}"

    def _headers(self, ct, length):
        return {"Content-Type": ct, "Content-Length": str(length)}

    def test_upload_page_needs_pin(self):
        status, body, _ = req(self.port, "GET", "/")
        self.assertEqual(status, 403)
        self.assertIn("访问码", body.decode("utf-8"))

    def test_upload_page_with_pin(self):
        status, body, _ = req(self.port, "GET", "/?pin=pin99")
        self.assertEqual(status, 200)
        self.assertIn("上传文件", body.decode("utf-8"))

    def test_upload_single_file(self):
        body, ct = self._upload_body([("hello.txt", b"landrop hello")])
        status, resp, headers = req(self.port, "POST", f"/upload?pin={self.token}",
                                    body, self._headers(ct, len(body)))
        self.assertIn(status, (200, 303))
        saved = self.out / "hello.txt"
        self.assertTrue(saved.exists())
        self.assertEqual(saved.read_bytes(), b"landrop hello")

    def test_upload_sanitizes_evil_names(self):
        body, ct = self._upload_body([("..\\..\\evil.txt", b"should be safe")])
        status, resp, _ = req(self.port, "POST", f"/upload?pin={self.token}",
                              body, self._headers(ct, len(body)))
        self.assertIn(status, (200, 303))
        # 文件必须落在接收目录内，且原路径不存在
        self.assertFalse((self.out.parent / "evil.txt").exists())
        self.assertFalse((self.out.parent.parent / "evil.txt").exists())
        self.assertTrue((self.out / "evil.txt").exists())

    def test_upload_multiple_files(self):
        body, ct = self._upload_body([("a.txt", b"AAA"), ("b.txt", b"BBB")])
        status, _, _ = req(self.port, "POST", f"/upload?pin={self.token}",
                           body, self._headers(ct, len(body)))
        self.assertIn(status, (200, 303))
        self.assertEqual((self.out / "a.txt").read_bytes(), b"AAA")
        self.assertEqual((self.out / "b.txt").read_bytes(), b"BBB")

    def test_upload_without_pin_403(self):
        body, ct = self._upload_body([("x.txt", b"X")])
        status, _, _ = req(self.port, "POST", "/upload",
                           body, self._headers(ct, len(body)))
        self.assertEqual(status, 403)

    def test_upload_not_multipart_400(self):
        status, _, _ = req(self.port, "POST", f"/upload?pin={self.token}",
                           b"data=1", {"Content-Type": "application/x-www-form-urlencoded",
                                       "Content-Length": "6"})
        self.assertEqual(status, 400)


class PeersEndpointTest(unittest.TestCase):
    """"/api/peers"：mDNS 发现端点（用假 Browser，不碰真实组播）。"""

    @classmethod
    def setUpClass(cls):
        import shutil
        cls.out = Path(tempfile.mkdtemp(prefix="lr-peers-"))

        class FakeBrowser:
            available = True

            @staticmethod
            def peers():
                return [{"instance": "测试机", "ip": "192.0.2.9", "port": 8800,
                         "hostname": "test.local.",
                         "txt": {"mode": "share", "open": "1"}}]

        cls.port = 8893
        cls.server = make_receive_server(str(cls.out), token="", host=HOST,
                                         port=cls.port, quiet=True,
                                         browser=FakeBrowser())
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        import shutil
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_peers_shape(self):
        status, body, _ = req(self.port, "GET", "/api/peers")
        self.assertEqual(status, 200)
        import json
        j = json.loads(body.decode("utf-8"))
        self.assertTrue(j["available"])
        self.assertEqual(j["peers"][0]["instance"], "测试机")
        self.assertTrue(j["peers"][0]["open"])
        self.assertEqual(j["peers"][0]["mode"], "share")

    def test_peers_without_browser(self):
        import json
        import shutil
        import tempfile
        out2 = Path(tempfile.mkdtemp(prefix="lr-peers2-"))
        srv = make_receive_server(str(out2), token="", host=HOST, port=8894,
                                  quiet=True, browser=None)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            status, body, _ = req(8894, "GET", "/api/peers")
            j = json.loads(body.decode("utf-8"))
            self.assertEqual(status, 200)
            self.assertFalse(j["available"])
        finally:
            srv.shutdown()
            srv.server_close()
            shutil.rmtree(out2, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
