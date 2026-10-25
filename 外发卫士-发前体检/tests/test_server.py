"""multipart 解析 + HTTP 服务全链路测试（上传→体检→净化→下载→复检）。"""

import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from helpers import build_docx

from inspector import server as srv
from inspector.multipart import build_multipart, parse_multipart


class TestMultipart(unittest.TestCase):
    def test_roundtrip(self):
        body, ctype = build_multipart("file", "报告.docx", b"\x00\x01binary\xff")
        parts = parse_multipart(body, ctype)
        self.assertIn("file", parts)
        name, content = parts["file"]
        self.assertEqual(name, "报告.docx")
        self.assertEqual(content, b"\x00\x01binary\xff")

    def test_no_boundary(self):
        self.assertEqual(parse_multipart(b"x", "text/plain"), {})


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 上传工作区指到临时目录，保持仓库干净
        patcher = mock.patch.object(srv, "UPLOAD_DIR", Path(self.tmp.name) / "uploads")
        patcher.start()
        self.addCleanup(patcher.stop)

        self.docx = build_docx(Path(self.tmp.name) / "src" / "a.docx")
        self.server = srv.make_server(0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.read(), dict(r.getheaders())
        finally:
            conn.close()

    def _post(self, path, body, ctype):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request("POST", path, body=body,
                         headers={"Content-Type": ctype})
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def test_page(self):
        status, body, _ = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("外发卫士".encode("utf-8"), body)

    def test_check_and_sanitize_and_download(self):
        content = self.docx.read_bytes()
        body, ctype = build_multipart("file", "方案.docx", content)
        status, resp = self._post("/api/check", body, ctype)
        self.assertEqual(status, 200)
        data = json.loads(resp.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["name"], "方案.docx")
        self.assertGreater(data["report"]["counts"]["high"], 0)

        uid = data["id"]
        status, resp = self._post("/api/sanitize", json.dumps({"id": uid}).encode(),
                                  "application/json")
        data2 = json.loads(resp.decode("utf-8"))
        self.assertTrue(data2["ok"], data2)
        self.assertEqual(data2["recheck"]["counts"]["high"], 0)
        self.assertEqual(data2["recheck"]["counts"]["med"], 0)

        status, blob, headers = self._get(data2["download"])
        self.assertEqual(status, 200)
        self.assertGreater(len(blob), 100)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))

    def test_check_unsupported(self):
        body, ctype = build_multipart("file", "x.exe", b"MZ...")
        status, resp = self._post("/api/check", body, ctype)
        data = json.loads(resp.decode("utf-8"))
        self.assertFalse(data["ok"])
        self.assertIn("不支持", data["error"])

    def test_report_endpoint(self):
        body, ctype = build_multipart("file", "r.docx", self.docx.read_bytes())
        _, resp = self._post("/api/check", body, ctype)
        uid = json.loads(resp.decode("utf-8"))["id"]
        status, body2, _ = self._get(f"/api/report/{uid}")
        self.assertEqual(status, 200)
        self.assertIn("counts", json.loads(body2.decode("utf-8")))

    def test_report_bad_id_404(self):
        status, _, _ = self._get("/api/report/nonexistent")
        self.assertEqual(status, 404)

    def test_sanitize_bad_id(self):
        status, resp = self._post("/api/sanitize", b'{"id":"nope"}', "application/json")
        data = json.loads(resp.decode("utf-8"))
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()
