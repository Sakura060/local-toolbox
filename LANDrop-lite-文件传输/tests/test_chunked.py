"""分块上传协议测试（v0.3）。"""

import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from landrop import server as lrs
from landrop.server import make_receive_server
from test_server import HOST, req   # 复用受控请求助手


def json_req(port, path, payload, method="POST"):
    body = json.dumps(payload).encode()
    return req(port, method, path, body,
               {"Content-Type": "application/json", "Content-Length": str(len(body))})


class ChunkedUploadTest(unittest.TestCase):
    PIN = "ck55"

    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="lr-chunk-"))
        cls.port = 8894
        cls.server = make_receive_server(str(cls.out), token=cls.PIN,
                                         host=HOST, port=cls.port, quiet=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.out, ignore_errors=True)

    def _init(self, filename, size, pin=PIN):
        return json_req(self.port, f"/upload-init?pin={pin}",
                        {"filename": filename, "size": size})

    def _chunk(self, uid, n, data, pin=PIN):
        q = f"&pin={pin}" if pin else ""
        return req(self.port, "POST", f"/upload-chunk?id={uid}&n={n}{q}",
                   data, {"Content-Length": str(len(data))})

    def _finish(self, uid, pin=PIN):
        return json_req(self.port, f"/upload-finish?pin={pin}", {"id": uid})

    def test_happy_path_small_chunks(self):
        blob = b"0123456789" * 1000                     # 10000 字节
        chunk = 3000                                     # 服务端固定 4MB？直接整块发
        # 服务端建议块大小是 4MB；这里 size<blob 用单块发不完，
        # 因此按服务端建议块大小切
        status, resp, _ = self._init("big.dat", len(blob))
        self.assertEqual(status, 200)
        info = json.loads(resp)
        uid, cs, total = info["id"], info["chunk_size"], info["total_chunks"]
        self.assertEqual(total, -(-len(blob) // cs))

        for n in range(total):
            part = blob[n * cs:(n + 1) * cs]
            s, r, _ = self._chunk(uid, n, part)
            self.assertEqual(s, 200)
            self.assertEqual(json.loads(r)["received"], n + 1)

        status, resp, _ = self._finish(uid)
        self.assertEqual(status, 200)
        saved = json.loads(resp)["saved"]
        self.assertEqual(saved, "big.dat")
        self.assertEqual((self.out / "big.dat").read_bytes(), blob)
        # 本会话的临时文件应已被改名落盘
        self.assertFalse((self.out / ".landrop-uploads" / f"{uid}.part").exists())

    def test_out_of_order_chunk_rejected(self):
        _, resp, _ = self._init("oo.dat", 10000)
        uid = json.loads(resp)["id"]
        status, _, _ = self._chunk(uid, 1, b"skipped zero")   # 直接发第 2 块
        self.assertEqual(status, 409)
        status, resp, _ = self._finish(uid)
        self.assertEqual(status, 409)                          # 未完成不可 finish

    def test_unknown_session_404(self):
        # 无 pin → 403（鉴权在前）；带 pin 但会话不存在 → 404
        status, _, _ = self._chunk("no-such-id", 0, b"x", pin=None)
        self.assertEqual(status, 403)
        status, _, _ = self._chunk("no-such-id", 0, b"x")
        self.assertEqual(status, 404)
        status, _, _ = self._finish("no-such-id")
        self.assertEqual(status, 404)

    def test_finish_incomplete_409(self):
        _, resp, _ = self._init("half.dat", 10000)
        uid = json.loads(resp)["id"]
        self._chunk(uid, 0, b"only part")
        status, _, _ = self._finish(uid)
        self.assertEqual(status, 409)
        # 临时文件应仍在
        parts = list((self.out / ".landrop-uploads").glob("*.part"))
        self.assertEqual(len(parts), 1)

    def test_abort_cleans_temp(self):
        _, resp, _ = self._init("abort.dat", 10000)
        uid = json.loads(resp)["id"]
        self._chunk(uid, 0, b"part")
        status, resp, _ = json_req(self.port, f"/upload-abort?pin={self.PIN}",
                                   {"id": uid})
        self.assertEqual(status, 200)
        parts = list((self.out / ".landrop-uploads").glob("*.part"))
        self.assertEqual(parts, [])

    def test_evil_name_sanitized_on_save(self):
        blob = b"evil" * 10
        _, resp, _ = self._init("..\\..\\evil.txt", len(blob))
        uid = json.loads(resp)["id"]
        self._chunk(uid, 0, blob)
        status, resp, _ = self._finish(uid)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(resp)["saved"], "evil.txt")
        self.assertEqual((self.out / "evil.txt").read_bytes(), blob)
        # 绝不能写到接收目录之外
        self.assertFalse((self.out.parent / "evil.txt").exists())

    def test_duplicate_finish_no_overwrite(self):
        blob1, blob2 = b"first" * 5, b"second" * 5
        _, resp, _ = self._init("dup.txt", len(blob1))
        uid1 = json.loads(resp)["id"]
        self._chunk(uid1, 0, blob1)
        self._finish(uid1)
        _, resp, _ = self._init("dup.txt", len(blob2))
        uid2 = json.loads(resp)["id"]
        self._chunk(uid2, 0, blob2)
        status, resp, _ = self._finish(uid2)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(resp)["saved"], "dup (1).txt")
        self.assertEqual((self.out / "dup.txt").read_bytes(), blob1)
        self.assertEqual((self.out / "dup (1).txt").read_bytes(), blob2)

    def test_oversize_rejected(self):
        status, _, _ = self._init("huge.bin", lrs.MAX_CHUNKED_TOTAL + 1)
        self.assertEqual(status, 400)

    def test_needs_pin(self):
        status, _, _ = self._init("x.bin", 10, pin="wrong")
        self.assertEqual(status, 403)

    def test_zero_byte_file(self):
        _, resp, _ = self._init("empty.txt", 0)
        uid = json.loads(resp)["id"]
        status, resp, _ = self._finish(uid)
        self.assertEqual(status, 200)
        self.assertTrue((self.out / "empty.txt").exists())


if __name__ == "__main__":
    unittest.main()
