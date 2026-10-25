"""securenames 单元测试：文件名清洗是 LANDrop 的安全核心。"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from landrop.securenames import safe_join, sanitize_filename


class SanitizeTest(unittest.TestCase):
    def test_plain_name_unchanged(self):
        self.assertEqual(sanitize_filename("photo.jpg"), "photo.jpg")
        self.assertEqual(sanitize_filename("报告 最终版.docx"), "报告 最终版.docx")

    def test_traversal_removed(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("..\\..\\windows\\system32\\evil.exe"), "evil.exe")
        self.assertEqual(sanitize_filename("../../../"), "unnamed")
        self.assertEqual(sanitize_filename("/absolute/path/file.txt"), "file.txt")
        self.assertEqual(sanitize_filename("C:\\Users\\x\\file.txt"), "file.txt")

    def test_dotfiles_and_dotdot(self):
        self.assertEqual(sanitize_filename(".."), "unnamed")
        self.assertEqual(sanitize_filename("."), "unnamed")
        self.assertEqual(sanitize_filename(".hidden"), "hidden")

    def test_illegal_chars_replaced(self):
        self.assertEqual(sanitize_filename('a<b>c:d"e|f?g*h.txt'), "a_b_c_d_e_f_g_h.txt")
        self.assertEqual(sanitize_filename("nul\x00byte.txt"), "nul_byte.txt")
        self.assertEqual(sanitize_filename("con\trol\x1f.txt"), "con_rol_.txt")

    def test_windows_reserved(self):
        self.assertEqual(sanitize_filename("CON"), "CON_")
        self.assertEqual(sanitize_filename("com1"), "com1_")
        self.assertEqual(sanitize_filename("lpt9.txt"), "lpt9_.txt")
        self.assertEqual(sanitize_filename("nul.tar.gz"), "nul_.tar.gz")   # stem=nul

    def test_length_limited(self):
        long_name = "长" * 300 + ".jpg"
        out = sanitize_filename(long_name)
        self.assertLessEqual(len(out), 125)
        self.assertTrue(out.endswith(".jpg"))

    def test_empty_and_none(self):
        self.assertEqual(sanitize_filename(""), "unnamed")
        self.assertEqual(sanitize_filename(None), "unnamed")
        self.assertEqual(sanitize_filename("   "), "unnamed")

    def test_never_raises(self):
        for weird in ["\\", "//", "...", "???", "*", "：", "a" * 5000]:
            sanitize_filename(weird)   # 不应抛异常


class SafeJoinTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.base = Path(tempfile.mkdtemp(prefix="ff-landrop-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def test_normal_join(self):
        out = safe_join(str(self.base), "hello.txt")
        self.assertEqual(Path(out).parent, self.base.resolve())

    def test_traversal_blocked(self):
        # 清洗后绝不含路径成分或父目录引用
        for bad in ["../../escape.txt", "..\\..\\x.txt", "/etc/passwd", ".."]:
            cleaned = sanitize_filename(bad)
            self.assertNotIn("..", cleaned)
            self.assertNotIn("/", cleaned)
            self.assertNotIn("\\", cleaned)


if __name__ == "__main__":
    unittest.main()
