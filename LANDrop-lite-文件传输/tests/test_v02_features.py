"""v0.2 功能测试：Range 断点续传、HEAD、文本快传。"""

import http.client
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from landrop.server import _parse_range, make_share_server, make_text_server
from test_server import HOST, req   # 复用受控请求助手


class ParseRangeTest(unittest.TestCase):
    def test_full_forms(self):
        self.assertEqual(_parse_range("bytes=0-4", 100), (0, 4))
        self.assertEqual(_parse_range("bytes=10-", 100), (10, 99))
        self.assertEqual(_parse_range("bytes=-5", 100), (95, 99))
        self.assertEqual(_parse_range("bytes=0-", 100), (0, 99))

    def test_end_clamped(self):
        self.assertEqual(_parse_range("bytes=90-200", 100), (90, 99))

    def test_invalid_or_unsatisfiable(self):
        self.assertIsNone(_parse_range("bytes=5-2", 100))     # 逆序
        self.assertIsNone(_parse_range("bytes=100-", 100))    # 起点越界
        self.assertIsNone(_parse_range("bytes=-0", 100))      # 非法后缀
        self.assertIsNone(_parse_range("bytes=-", 100))       # 空
        self.assertIsNone(_parse_range("item=1-2", 100))      # 非 bytes 单位
        self.assertIsNone(_parse_range("bytes=0-1,3-4", 100)) # 多段 → 不支持
        self.assertIsNone(_parse_range("bytes=-5", 0))        # 空文件


class RangeDownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="lr-range-"))
        cls.blob = bytes(range(256)) * 40          # 10240 字节
        (cls.root / "data.bin").write_bytes(cls.blob)
        cls.token = "rng777"
        cls.port = 8892
        cls.server = make_share_server(str(cls.root), token=cls.token,
                                       host=HOST, port=cls.port, quiet=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        import shutil
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_full_download_has_accept_ranges(self):
        status, body, headers = req(self.port, "GET", f"/s/{self.token}/data.bin")
        self.assertEqual(status, 200)
        self.assertEqual(body, self.blob)
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")

    def test_range_middle_chunk(self):
        status, body, headers = req(self.port, "GET",
                                    f"/s/{self.token}/data.bin",
                                    headers={"Range": "bytes=100-199"})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.blob[100:200])
        self.assertEqual(headers.get("Content-Range"), f"bytes 100-199/{len(self.blob)}")
        self.assertEqual(headers.get("Content-Length"), "100")

    def test_range_open_ended(self):
        status, body, _ = req(self.port, "GET", f"/s/{self.token}/data.bin",
                              headers={"Range": "bytes=10200-"})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.blob[10200:])

    def test_range_suffix(self):
        status, body, _ = req(self.port, "GET", f"/s/{self.token}/data.bin",
                              headers={"Range": "bytes=-10"})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.blob[-10:])

    def test_range_unsatisfiable_416(self):
        status, _, headers = req(self.port, "GET", f"/s/{self.token}/data.bin",
                                 headers={"Range": "bytes=999999-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers.get("Content-Range"), f"bytes */{len(self.blob)}")

    def test_resume_chain(self):
        """模拟断点续传：先取 0-99，再从 100 取到结尾。"""
        s1, p1, _ = req(self.port, "GET", f"/s/{self.token}/data.bin",
                        headers={"Range": "bytes=0-99"})
        self.assertEqual((s1, p1), (206, self.blob[:100]))
        s2, p2, _ = req(self.port, "GET", f"/s/{self.token}/data.bin",
                        headers={"Range": f"bytes=100-{len(self.blob) - 1}"})
        self.assertEqual(s2, 206)
        self.assertEqual(p1 + p2, self.blob)

    def test_head_request_no_body(self):
        status, body, headers = req(self.port, "HEAD", f"/s/{self.token}/data.bin")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Content-Length"), str(len(self.blob)))
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")

    def test_head_listing(self):
        status, body, headers = req(self.port, "HEAD", f"/s/{self.token}")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")


class TextShareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = "txt123"
        cls.port = 8893
        cls.text = "第一行\nSecond line with emoji ✅\n第三行"
        cls.server = make_text_server(cls.text, token=cls.token,
                                      host=HOST, port=cls.port, quiet=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_text_page(self):
        status, body, _ = req(self.port, "GET", f"/s/{self.token}")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn("文本快传", html)
        self.assertIn("Second line", html)
        self.assertIn("复制全文", html)

    def test_text_raw(self):
        status, body, headers = req(self.port, "GET", f"/s/{self.token}/raw")
        self.assertEqual(status, 200)
        self.assertEqual(body.decode("utf-8"), self.text)
        self.assertIn("text/plain", headers.get("Content-Type", ""))

    def test_wrong_token_403(self):
        status, _, _ = req(self.port, "GET", "/s/wrong")
        self.assertEqual(status, 403)

    def test_unknown_subpath_404(self):
        status, _, _ = req(self.port, "GET", f"/s/{self.token}/other")
        self.assertEqual(status, 404)

    def test_pin_query_also_accepted(self):
        status, _, _ = req(self.port, "GET", f"/s/wrongtoken?pin={self.token}")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
