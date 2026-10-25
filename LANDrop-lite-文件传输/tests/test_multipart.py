"""multipart 解析单元测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from landrop.multipart import parse_multipart

B = b"----WebKitFormBoundary7MA4YWxkTrZu0gW"


def build(parts: list[tuple[str, str | None, bytes]]) -> bytes:
    """构造 multipart 请求体。parts: (name, filename, data)"""
    out = b""
    for name, filename, data in parts:
        out += b"--" + B + b"\r\n"
        disp = f'form-data; name="{name}"'
        if filename is not None:
            disp += f'; filename="{filename}"'
        out += f'Content-Disposition: {disp}\r\n'.encode()
        if filename is not None:
            out += b"Content-Type: application/octet-stream\r\n"
        out += b"\r\n" + data + b"\r\n"
    out += b"--" + B + b"--\r\n"
    return out


CT = f"multipart/form-data; boundary={B.decode()}"


class MultipartTest(unittest.TestCase):
    def test_single_file(self):
        body = build([("files", "hello.txt", b"hello world")])
        r = parse_multipart(body, CT)
        self.assertEqual(len(r.files), 1)
        self.assertEqual(r.files[0].filename, "hello.txt")
        self.assertEqual(r.files[0].data, b"hello world")

    def test_multiple_files_and_field(self):
        body = build([
            ("note", None, b"some note"),
            ("files", "a.bin", b"\x00\x01\x02"),
            ("files", "b.bin", b"XYZ"),
        ])
        r = parse_multipart(body, CT)
        self.assertEqual(r.fields["note"], "some note")
        self.assertEqual([f.filename for f in r.files], ["a.bin", "b.bin"])
        self.assertEqual(r.files[0].data, b"\x00\x01\x02")
        self.assertEqual(r.files[1].data, b"XYZ")

    def test_binary_with_boundary_like_content(self):
        payload = b"data\r\n--" + B[:-5] + b"\r\nmore"
        body = build([("files", "x.bin", payload)])
        r = parse_multipart(body, CT)
        self.assertEqual(r.files[0].data, payload)

    def test_chinese_filename(self):
        body = build([("files", "中文文件.png", b"abc")])
        r = parse_multipart(body, CT)
        self.assertEqual(r.files[0].filename, "中文文件.png")

    def test_filename_with_quotes_escaped(self):
        # 客户端按 MIME 规范转义引号：filename="weird\".txt"
        B = b"----WebKitFormBoundary7MA4YWxkTrZu0gW"
        body = (b"--" + B + b"\r\n"
                b'Content-Disposition: form-data; name="files"; filename="weird\\".txt"\r\n'
                b"\r\nq\r\n--" + B + b"--\r\n")
        r = parse_multipart(body, f"multipart/form-data; boundary={B.decode()}")
        self.assertEqual(r.files[0].filename, 'weird".txt')

    def test_no_boundary_raises(self):
        with self.assertRaises(ValueError):
            parse_multipart(b"whatever", "multipart/form-data")

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_multipart(b"not multipart at all", CT)

    def test_empty_body_raises(self):
        with self.assertRaises(ValueError):
            parse_multipart(b"", CT)


if __name__ == "__main__":
    unittest.main()
