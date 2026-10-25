"""Planner 单元测试：计划生成、冲突消解、自锁防护。"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fileflow.planner import Planner, human_size
from fileflow.rules import Rule, RuleEngine
from fileflow.scanner import Scanner


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ff-plan-"))
        self.engine = RuleEngine([
            Rule(name="图片", type="extension", target="图片", pattern="jpg png"),
            Rule(name="文档", type="extension", target="文档", pattern="pdf"),
        ])
        self.planner = Planner(self.engine)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, rel: str, content: str = "x") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _plan(self):
        return self.planner.build(Scanner().scan(str(self.root)))

    def test_plan_moves(self):
        self._write("a.jpg")
        self._write("b.pdf")
        plan = self._plan()
        self.assertEqual(len(plan.moves), 2)
        dsts = {Path(a.dst).name: Path(a.dst).parent.name for a in plan.moves}
        self.assertEqual(dsts["a.jpg"], "图片")
        self.assertEqual(dsts["b.pdf"], "文档")

    def test_unmatched_stays(self):
        self._write("weird.xyz")
        plan = self._plan()
        self.assertEqual(plan.moves, [])
        self.assertEqual(len(plan.unmatched), 1)

    def test_conflict_resolution(self):
        self._write("图片/existing.jpg")     # 目标目录已有同名
        self._write("existing.jpg")          # → existing (1).jpg
        self._write("deep/existing.jpg")     # 跨目录同名 → existing (2).jpg
        plan = self._plan()
        names = sorted(Path(a.dst).name for a in plan.moves)
        self.assertEqual(names, ["existing (1).jpg", "existing (2).jpg"])
        self.assertEqual(plan.conflicts_resolved, 2)

    def test_already_in_target_no_move(self):
        self._write("图片/inner.jpg")
        plan = self._plan()
        self.assertEqual(plan.moves, [])
        self.assertEqual(len(plan.actions), 1)   # 记录为动作但不移动

    def test_plan_is_pure_dry_run(self):
        """生成计划不应改动文件系统。"""
        self._write("a.jpg")
        self._plan()
        self.assertTrue((self.root / "a.jpg").exists())
        self.assertFalse((self.root / "图片").exists())

    def test_subdir_files_get_flat_target(self):
        """嵌套文件也被整理到根下的分类目录。"""
        self._write("deep/nested/a.jpg")
        plan = self._plan()
        self.assertEqual(len(plan.moves), 1)
        self.assertEqual(Path(plan.moves[0].dst).parent.name, "图片")

    def test_human_size(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(2048), "2.0 KB")
        self.assertEqual(human_size(3 * 1024 * 1024), "3.0 MB")


if __name__ == "__main__":
    unittest.main()
