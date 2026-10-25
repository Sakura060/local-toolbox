"""Scanner 单元测试。"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fileflow.scanner import Scanner


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ff-scan-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel: str, content: str = "x") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_basic_scan(self):
        self._write("a.txt", "12345")
        self._write("sub/b.jpg")
        scan = Scanner().scan(str(self.root))
        self.assertEqual(len(scan.files), 2)
        names = {f.name for f in scan.files}
        self.assertEqual(names, {"a.txt", "b.jpg"})
        self.assertEqual(scan.total_size, 6)
        self.assertEqual(scan.errors, [])

    def test_suffix_and_stem(self):
        self._write("report.final.docx")
        self._write(".gitignore")
        self._write("noext")
        scan = Scanner().scan(str(self.root))
        info = {f.name: f for f in scan.files}
        self.assertEqual(info["report.final.docx"].suffix, "docx")
        self.assertEqual(info["report.final.docx"].stem, "report.final")
        self.assertEqual(info[".gitignore"].suffix, "")     # 点开头不算扩展名
        self.assertEqual(info["noext"].suffix, "")
        self.assertEqual(info["noext"].stem, "noext")

    def test_hidden_dirs_skipped(self):
        self._write(".hidden/x.txt")
        self._write("normal.txt")
        scan = Scanner().scan(str(self.root))
        self.assertEqual(len(scan.files), 1)
        self.assertEqual(len(scan.skipped_dirs), 1)

    def test_hidden_dirs_kept_when_disabled(self):
        self._write(".hidden/x.txt")
        scan = Scanner(skip_hidden=False).scan(str(self.root))
        self.assertEqual(len(scan.files), 1)

    def test_exclude_dirs_and_patterns(self):
        self._write("node_modules/lib.js")
        self._write("keep.js")
        self._write("cache.tmp")
        scan = Scanner(exclude_dirs=["node_modules"],
                       exclude_patterns=["*.tmp"]).scan(str(self.root))
        self.assertEqual([f.name for f in scan.files], ["keep.js"])

    def test_depth_limit(self):
        deep = self.root
        for _ in range(20):
            deep = deep / "d"
        deep.mkdir(parents=True)
        (deep / "x.txt").write_text("x")
        scan = Scanner(max_depth=3).scan(str(self.root))
        self.assertEqual(scan.files, [])
        self.assertTrue(any("深度" in e for e in scan.errors))

    def test_chinese_names(self):
        self._write("中文 文件名.txt")
        scan = Scanner().scan(str(self.root))
        self.assertEqual(scan.files[0].name, "中文 文件名.txt")
        self.assertEqual(scan.files[0].suffix, "txt")

    def test_invalid_root(self):
        with self.assertRaises(NotADirectoryError):
            Scanner().scan(str(self.root / "不存在"))
        with self.assertRaises(NotADirectoryError):
            Scanner().scan(str(self._write("file.txt")))  # 文件不是目录


if __name__ == "__main__":
    unittest.main()
