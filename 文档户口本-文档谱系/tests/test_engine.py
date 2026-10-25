"""引擎（扫描→聚类→谱系落库）测试。"""

import tempfile
import unittest
from pathlib import Path

from helpers import build_docx, set_mtime

from docline.engine import Database, Engine, rebuild_clusters

T0 = 1_700_000_000.0


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        self.root.mkdir()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.db = Database(self.data / "t.db")
        self.engine = Engine(self.db)

        # 演化链 + 重复副本 + 无关文档
        build_docx(self.root / "方案v1.docx", ["项目背景说明文字", "预算总额一百二十万"])
        build_docx(self.root / "方案v2.docx", ["项目背景说明文字",
                                             "预算总额调整为一百三十五万元",
                                             "新增验收标准章节"])
        build_docx(self.root / "方案v2 (1).docx", ["项目背景说明文字",
                                                  "预算总额调整为一百三十五万元",
                                                  "新增验收标准章节"])
        build_docx(self.root / "购物清单.docx", ["排骨 两斤", "番茄 三个"])
        for i, name in enumerate(["方案v1.docx", "方案v2.docx",
                                  "方案v2 (1).docx", "购物清单.docx"]):
            set_mtime(self.root / name, T0 + i * 100)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_scan_and_cluster(self):
        s = self.engine.scan([str(self.root)])
        self.assertEqual(s["processed"], 4)
        st = self.db.stats()
        self.assertEqual(st["docs"], 4)
        self.assertGreaterEqual(st["clusters"], 2)  # 方案簇 + 清单簇

        clusters = [c for c in self.db.list_clusters() if c["size"] > 1]
        self.assertEqual(len(clusters), 1)
        c = clusters[0]
        self.assertEqual(c["size"], 3)
        self.assertIn("方案v2", c["latest_name"])
        self.assertFalse(c["fork"])

        members = self.db.docs_by_cluster(c["cid"])
        names = {m["name"] for m in members}
        self.assertIn("方案v1.docx", names)
        self.assertIn("方案v2 (1).docx", names)

    def test_rescan_idempotent(self):
        self.engine.scan([str(self.root)])
        s2 = self.engine.scan([str(self.root)])
        self.assertEqual(s2["skipped"], 4)
        self.assertEqual(s2["processed"], 0)
        self.assertEqual(self.db.stats()["docs"], 4)

    def test_delete_purges(self):
        self.engine.scan([str(self.root)])
        (self.root / "购物清单.docx").unlink()
        s = self.engine.scan([str(self.root)])
        self.assertEqual(s["purged"], 1)
        self.assertEqual(self.db.stats()["docs"], 3)

    def test_get_doc_blocks(self):
        self.engine.scan([str(self.root)])
        doc = next(d for d in self.db.all_docs() if d["name"] == "方案v2.docx")
        got = self.db.get_doc(doc["id"])
        self.assertIn("新增验收标准章节", got["blocks"])

    def test_rebuild_only(self):
        self.engine.scan([str(self.root)])
        n = rebuild_clusters(self.db)
        self.assertGreaterEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
