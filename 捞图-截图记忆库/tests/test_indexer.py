"""indexer 测试：注入假 OCR/缩略图，跑全流程（不依赖真模型）。"""

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from laotu.db import Database
from laotu.indexer import Indexer


def fake_ocr_factory(text="红色 优惠券 单号2026"):
    def ocr(path):
        return (text, "")
    return ocr


def fake_thumb(path, dest, max_side=360):
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 8)).save(dest, "JPEG")
    return (10, 8)


def fake_dims(path):
    return (10, 8)


def make_png(p: Path, color=(200, 60, 60)):
    Image.new("RGB", (32, 32), color).save(p, "PNG")


class TestIndexer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "shots"
        self.root.mkdir()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.db = Database(self.data / "t.db")
        self.idx = Indexer(self.db, ocr_func=fake_ocr_factory(),
                           thumb_func=fake_thumb, dim_func=fake_dims,
                           thumb_dir=self.data / "thumbs")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_first_scan(self):
        make_png(self.root / "a.png")
        make_png(self.root / "b.png")
        (self.root / "note.txt").write_text("ignore me", encoding="utf-8")
        s = self.idx.scan([str(self.root)])
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["updated"], 2)
        self.assertEqual(s["errors"], 0)
        self.assertEqual(self.db.stats()["total"], 2)
        # 根目录已登记
        self.assertIn(str(self.root), [str(r) for r in self.db.list_roots()])

    def test_incremental_and_purge(self):
        a = self.root / "a.png"
        b = self.root / "b.png"
        make_png(a)
        make_png(b)
        self.idx.scan([str(self.root)])

        # 未变化 → 全跳过
        s = self.idx.scan([str(self.root)])
        self.assertEqual(s["skipped"], 2)
        self.assertEqual(s["processed"], 0)

        # 修改 a → 只处理 a
        make_png(a, color=(10, 200, 30))
        s = self.idx.scan([str(self.root)])
        self.assertEqual(s["processed"], 1)
        self.assertEqual(s["skipped"], 1)

        # 删除 b → purge 1
        b.unlink()
        s = self.idx.scan([str(self.root)])
        self.assertEqual(s["purged"], 1)
        self.assertEqual(self.db.stats()["total"], 1)

    def test_ocr_error_recorded_not_fatal(self):
        def bad_ocr(path):
            return ("", "Boom: broken")

        idx = Indexer(self.db, ocr_func=bad_ocr, thumb_func=fake_thumb,
                      dim_func=fake_dims, thumb_dir=self.data / "thumbs")
        make_png(self.root / "bad.png")
        s = idx.scan([str(self.root)])
        self.assertEqual(s["errors"], 1)
        r = self.db.search("bad")
        self.assertEqual(r["items"][0]["ocr_ok"], 0)
        self.assertIn("Boom", r["items"][0]["err"])

    def test_name_is_searchable(self):
        make_png(self.root / "微信截图_20260926.png")
        self.idx.scan([str(self.root)])
        r = self.db.search("微信截图")
        self.assertEqual(r["total"], 1)


if __name__ == "__main__":
    unittest.main()
