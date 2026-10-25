"""EXIF / PDF / ZIP 体检与图片净化测试。"""

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from inspector.inspect import inspect_file, sanitize_file


def _make_jpeg_with_exif(p: Path) -> None:
    img = Image.new("RGB", (64, 64), (180, 90, 90))
    ex = img.getexif()
    ex[271] = "Canon"       # Make
    ex[272] = "EOS R5"      # Model
    ex[305] = "Photoshop"   # Software
    ex[306] = "2026:09:26 12:00:00"  # DateTime
    gps = ex.get_ifd(0x8825)
    gps[2] = (31.0, 13.0, 24.0)   # 纬度 d/m/s
    gps[1] = "N"
    gps[4] = (121.0, 28.0, 5.0)   # 经度
    gps[3] = "E"
    img.save(p, "JPEG", exif=ex)


class TestExif(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_gps_detected(self):
        p = self.d / "a.jpg"
        _make_jpeg_with_exif(p)
        rep = inspect_file(p)
        kinds = {f.kind for f in rep.findings}
        for k in ("exif_gps", "exif_device", "exif_author", "exif_time"):
            self.assertIn(k, kinds, f"缺 {k}")
        gps = next(f for f in rep.findings if f.kind == "exif_gps")
        self.assertIn("31.", gps.detail)

    def test_no_exif_no_finding(self):
        p = self.d / "b.jpg"
        Image.new("RGB", (32, 32), "white").save(p, "JPEG")
        rep = inspect_file(p)
        self.assertEqual(rep.findings, [])

    def test_strip_roundtrip(self):
        p = self.d / "a.jpg"
        _make_jpeg_with_exif(p)
        dst = self.d / "a_s.jpg"
        result = sanitize_file(p, dst)
        self.assertIsNotNone(result)
        rep = inspect_file(dst)
        kinds = {f.kind for f in rep.findings}
        self.assertNotIn("exif_gps", kinds)
        self.assertNotIn("exif_device", kinds)
        self.assertTrue(dst.is_file())

    def test_png_unsupported_exif_report_only(self):
        # PNG 通常无 EXIF；确保不崩
        p = self.d / "c.png"
        Image.new("RGB", (16, 16), "white").save(p, "PNG")
        rep = inspect_file(p)
        self.assertTrue(all(f.level == "low" for f in rep.findings))


class TestPdf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pdf_findings(self):
        p = self.d / "a.pdf"
        p.write_bytes(
            b"%PDF-1.4\n"
            b"/Author (Zhang San)\n"
            b"/Title (Q4 Report)\n"
            b"/Producer (SecretWriter 9.0)\n"
            b"/EmbeddedFiles true\n"
            b"/JavaScript true\n"
            b"%%EOF")
        rep = inspect_file(p)
        kinds = {f.kind for f in rep.findings}
        for k in ("pdf_author", "pdf_attachments", "pdf_js"):
            self.assertIn(k, kinds, f"缺 {k}")
        au = next(f for f in rep.findings if f.kind == "pdf_author")
        self.assertIn("Zhang San", au.detail)

    def test_pdf_sanitize_returns_none(self):
        p = self.d / "a.pdf"
        p.write_bytes(b"%PDF-1.4\n%%EOF")
        self.assertIsNone(sanitize_file(p, self.d / "s.pdf"))


class TestZip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_zip_paths(self):
        import zipfile

        p = self.d / "a.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("Users/zhangsan/Documents/secret.txt", "x")
            zf.writestr("readme.md", "y")
        rep = inspect_file(p)
        zp = next(f for f in rep.findings if f.kind == "zip_paths")
        self.assertIn("zhangsan", zp.detail)


if __name__ == "__main__":
    unittest.main()
