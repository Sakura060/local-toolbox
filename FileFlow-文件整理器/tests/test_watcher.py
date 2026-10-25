"""watch 监控模式测试。"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fileflow.executor import LOG_DIR as _REAL_LOG_DIR
from fileflow.rules import Rule, RuleEngine
from fileflow.scanner import Scanner
from fileflow.watcher import Watcher


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ff-watch-"))
        self.logs_tmp = Path(tempfile.mkdtemp(prefix="ff-wlogs2-"))
        self._log_backup = _REAL_LOG_DIR
        import fileflow.executor as ex
        ex.LOG_DIR = str(self.logs_tmp)
        self.engine = RuleEngine([
            Rule(name="图片", type="extension", target="图片", pattern="jpg"),
        ])
        self.events: list[str] = []
        self.now = 1_000_000.0

    def tearDown(self):
        import fileflow.executor as ex
        ex.LOG_DIR = self._log_backup
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.logs_tmp, ignore_errors=True)

    def _write(self, rel: str, content: str = "x", age: float = 9999.0) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        ts = self.now - age
        os.utime(p, (ts, ts))
        return p

    def _watcher(self, min_age: float = 5.0) -> Watcher:
        return Watcher(str(self.root), self.engine, Scanner(),
                       interval=1.0, min_age=min_age,
                       on_event=self.events.append)

    def test_moves_settled_files(self):
        self._write("a.jpg")
        w = self._watcher()
        moved = w.cycle_once(now=self.now)
        self.assertEqual(moved, 1)
        self.assertTrue((self.root / "图片" / "a.jpg").exists())
        self.assertEqual(w.stats.moved_total, 1)
        self.assertEqual(w.stats.cycles, 1)
        self.assertEqual(len(w.stats.runs), 1)       # 有日志可撤销

    def test_min_age_skips_fresh_files(self):
        self._write("fresh.jpg", age=1.0)    # 1 秒前刚写入
        self._write("old.jpg", age=60.0)
        w = self._watcher(min_age=5.0)
        moved = w.cycle_once(now=self.now)
        self.assertEqual(moved, 1)
        self.assertFalse((self.root / "图片" / "fresh.jpg").exists())
        self.assertTrue((self.root / "fresh.jpg").exists())       # 太新，不动
        self.assertTrue((self.root / "图片" / "old.jpg").exists())
        self.assertEqual(w.stats.skipped_fresh, 1)

    def test_fresh_file_moved_after_aging(self):
        self._write("late.jpg", age=1.0)
        w = self._watcher(min_age=5.0)
        self.assertEqual(w.cycle_once(now=self.now), 0)
        # 时间前进 10 秒后，文件已静置足够久
        self.assertEqual(w.cycle_once(now=self.now + 10), 1)
        self.assertTrue((self.root / "图片" / "late.jpg").exists())
        self.assertEqual(w.stats.cycles, 2)

    def test_uses_config_excludes(self):
        """scanner 自带排除规则（如 *.tmp）时同样生效。"""
        scanner = Scanner(exclude_patterns=["*.tmp"])
        w = Watcher(str(self.root), self.engine, scanner, min_age=0)
        self._write("keep.jpg")
        self._write("skip.tmp")
        w.cycle_once(now=self.now)
        self.assertTrue((self.root / "图片" / "keep.jpg").exists())
        self.assertTrue((self.root / "skip.tmp").exists())

    def test_already_organized_no_op(self):
        self._write("a.jpg")
        w = self._watcher()
        w.cycle_once(now=self.now)
        self.assertEqual(w.cycle_once(now=self.now), 0)   # 第二轮无事可做
        self.assertEqual(w.stats.moved_total, 1)

    def test_event_messages_emitted(self):
        self._write("a.jpg")
        w = self._watcher()
        w.cycle_once(now=self.now)
        self.assertTrue(any("整理 1 个文件" in e for e in self.events))


if __name__ == "__main__":
    unittest.main()
