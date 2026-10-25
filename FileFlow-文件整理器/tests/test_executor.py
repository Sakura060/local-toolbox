"""执行器单元测试：执行、日志、undo、越界防护、容错。"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fileflow import executor
from fileflow.executor import apply_plan, load_log, undo
from fileflow.planner import Action, Plan
from fileflow.rules import Rule, RuleEngine
from fileflow.scanner import Scanner


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ff-exec-"))
        self.logs_tmp = Path(tempfile.mkdtemp(prefix="ff-logs-"))
        # 把日志目录隔离到临时位置，避免污染真实 ~/.fileflow
        self._log_dir_backup = executor.LOG_DIR
        executor.LOG_DIR = str(self.logs_tmp)
        self.engine = RuleEngine([
            Rule(name="图片", type="extension", target="图片", pattern="jpg"),
        ])

    def tearDown(self):
        executor.LOG_DIR = self._log_dir_backup
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.logs_tmp, ignore_errors=True)

    def _write(self, rel: str, content: str = "x") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _plan(self) -> Plan:
        from fileflow.planner import Planner
        return Planner(self.engine).build(Scanner().scan(str(self.root)))

    def test_apply_and_undo_roundtrip(self):
        self._write("a.jpg")
        self._write("b.jpg")
        plan = self._plan()
        report = apply_plan(plan)
        self.assertEqual(report.moved, 2)
        self.assertEqual(report.failed, 0)
        self.assertFalse((self.root / "a.jpg").exists())
        self.assertTrue((self.root / "图片" / "a.jpg").exists())

        # 日志落盘且结构正确
        data = load_log(report.log_path)
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["actions"]), 2)

        rep = undo(report.log_path)
        self.assertEqual(rep.restored, 2)
        self.assertTrue((self.root / "a.jpg").exists())
        self.assertFalse((self.root / "图片").exists())   # 空目录被清理

    def test_undo_with_occupied_original(self):
        """原位置被同名新文件占用时，回滚到 (restored-N)。"""
        src = self._write("a.jpg")
        plan = self._plan()
        report = apply_plan(plan)
        moved_to = self.root / "图片" / "a.jpg"
        self.assertTrue(moved_to.exists())
        src.write_text("new occupant", encoding="utf-8")   # 原位置出现新文件

        rep = undo(report.log_path)
        self.assertEqual(rep.restored, 1)
        restored = self.root / "a (restored-1).jpg"
        self.assertTrue(restored.exists())
        self.assertEqual(restored.read_text(encoding="utf-8"), "x")
        self.assertEqual(src.read_text(encoding="utf-8"), "new occupant")

    def test_dry_run_touches_nothing(self):
        self._write("a.jpg")
        plan = self._plan()
        report = apply_plan(plan, dry_run=True)
        self.assertEqual(report.moved, 1)
        self.assertTrue((self.root / "a.jpg").exists())

    def test_out_of_root_target_rejected(self):
        """规则目标试图把文件移出根目录 → 执行期拒绝。"""
        outside = Path(tempfile.mkdtemp(prefix="ff-outside-"))
        try:
            self._write("a.jpg")
            plan = self._plan()
            # 篡改第一个动作的目标到根目录之外
            plan.actions[0].dst = str(outside / "stolen.jpg")
            report = apply_plan(plan)
            self.assertEqual(report.moved, 0)
            self.assertEqual(report.failed, 1)
            self.assertTrue((self.root / "a.jpg").exists())
            self.assertFalse((outside / "stolen.jpg").exists())
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_tampered_log_undo_rejected(self):
        """被篡改的日志（原路径越界）→ undo 拒绝该条目。"""
        self._write("a.jpg")
        plan = self._plan()
        report = apply_plan(plan)
        log_data = load_log(report.log_path)
        log_data["actions"][0]["src"] = r"C:\Windows\evil.jpg"
        with open(report.log_path, "w", encoding="utf-8") as fp:
            json.dump(log_data, fp)
        rep = undo(report.log_path)
        self.assertEqual(rep.restored, 0)
        self.assertEqual(rep.failed, 1)
        self.assertTrue((self.root / "图片" / "a.jpg").exists())   # 未被移走

    def test_missing_source_skipped(self):
        self._write("a.jpg")
        plan = self._plan()
        os.remove(plan.moves[0].src)     # 计划后文件消失
        report = apply_plan(plan)
        self.assertEqual(report.moved, 0)
        self.assertEqual(report.skipped, 1)

    def test_single_failure_does_not_stop_rest(self):
        """第一条移动失败（模拟磁盘错误），其余仍执行。"""
        self._write("a.jpg")
        self._write("b.jpg")
        plan = self._plan()
        real_move = shutil.move
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("simulated disk error")
            return real_move(src, dst)

        with mock.patch("fileflow.executor.shutil.move", side_effect=flaky):
            report = apply_plan(plan)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.moved, 1)
        self.assertTrue(any("simulated" in f for f in report.failures))

    def test_load_log_invalid(self):
        with self.assertRaises(FileNotFoundError):
            load_log(str(self.logs_tmp / "nope.json"))
        bad = self.logs_tmp / "bad.json"
        bad.write_text('{"version": 9}', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_log(str(bad))

    def test_log_path_sanitized(self):
        p = executor.log_path_for(r"D:\evil dir/..\\weird??name")
        name = os.path.basename(p)
        self.assertRegex(name, r"^[A-Za-z0-9_\-]{1,60}\.json$")
        self.assertTrue(os.path.dirname(p) == executor.LOG_DIR)


if __name__ == "__main__":
    unittest.main()
