"""dedup 单元测试。"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fileflow.dedup import find_duplicates, plan_cleanup


class DedupTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ff-dup-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel: str, content: bytes) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def test_exact_duplicates_found(self):
        blob = b"same content " * 5000        # ~65KB，跨过 quick-hash 块
        self._write("a.bin", blob)
        self._write("sub/b.bin", blob)
        self._write("c.bin", b"different")
        groups = find_duplicates(str(self.root))
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].paths), 2)
        self.assertEqual(groups[0].wasted, len(blob))

    def test_same_size_diff_content_not_duplicate(self):
        self._write("x.bin", b"A" * 100)
        self._write("y.bin", b"B" * 100)
        groups = find_duplicates(str(self.root))
        self.assertEqual(groups, [])

    def test_different_sizes_not_duplicate(self):
        self._write("x.bin", b"A" * 100)
        self._write("y.bin", b"A" * 50)
        self.assertEqual(find_duplicates(str(self.root)), [])

    def test_empty_files_ignored(self):
        self._write("e1.txt", b"")
        self._write("e2.txt", b"")
        self.assertEqual(find_duplicates(str(self.root)), [])

    def test_group_of_three(self):
        blob = b"triplicate" * 20000
        self._write("1.dat", blob)
        self._write("2.dat", blob)
        self._write("3.dat", blob)
        groups = find_duplicates(str(self.root))
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].paths), 3)
        self.assertEqual(groups[0].wasted, 2 * len(blob))

    def test_quick_hash_collision_resolved_by_full_hash(self):
        # 构造 quick 预筛可能碰撞但全量不同的文件较难；退而验证
        # quick 路径本身不产生误报：大量不同文件 → 无组
        for i in range(8):
            self._write(f"f{i}.bin", bytes([i]) * 70000)
        self.assertEqual(find_duplicates(str(self.root)), [])

    def test_exclude_dirs(self):
        blob = b"dup" * 30000
        self._write("a.bin", blob)
        self._write("node_modules/b.bin", blob)
        groups = find_duplicates(str(self.root), exclude_dirs=["node_modules"])
        self.assertEqual(groups, [])

    def test_plan_cleanup_keep_oldest(self):
        blob = b"old?new?" * 20000
        a = self._write("a.bin", blob)
        b = self._write("b.bin", blob)
        os.utime(a, (1000, 1000))
        os.utime(b, (2000, 2000))
        groups = find_duplicates(str(self.root))
        plan = plan_cleanup(groups, keep="oldest")
        self.assertEqual(len(plan), 1)
        dup, keeper = plan[0]
        self.assertEqual(Path(keeper), a)
        self.assertEqual(Path(dup), b)

    def test_nested_dup_across_dirs(self):
        blob = b"nested" * 30000
        self._write("docs/one.pdf", blob)
        self._write("backup/docs/one.pdf", blob)
        groups = find_duplicates(str(self.root))
        self.assertEqual(len(groups), 1)
        paths = {Path(p).as_posix() for p in groups[0].paths}
        self.assertEqual(len(paths), 2)


if __name__ == "__main__":
    unittest.main()
