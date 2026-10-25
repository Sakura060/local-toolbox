"""db 测试：增删改查、指纹、purge、FTS 与 LIKE 兜底。"""

import tempfile
import unittest
from pathlib import Path

from laotu.db import Database


def _mk(db: Database, path: str, name: str, ocr: str, seg_text: str) -> int:
    return db.upsert_image(
        path=path, name=name, folder=str(Path(path).parent),
        size=10, mtime_ns=1000, width=100, height=80,
        ocr_text=ocr, ocr_ok=1 if ocr else 0, err=None, seg_text=seg_text,
    )


class TestDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_upsert_idempotent_by_path(self):
        i1 = _mk(self.db, r"C:\pics\a.png", "a.png", "红色优惠券", "红色 优惠券")
        i2 = _mk(self.db, r"C:\pics\a.png", "a.png", "红色优惠券改", "红色 优惠券 改")
        self.assertEqual(i1, i2)
        st = self.db.stats()
        self.assertEqual(st["total"], 1)

    def test_search_fts_chinese(self):
        _mk(self.db, r"C:\pics\a.png", "a.png", "快递单号2026", "快递 单号 2026")
        _mk(self.db, r"C:\pics\b.png", "b.png", "今天天气不错", "今天 天气 不错")
        r = self.db.search("单号")
        self.assertEqual(r["mode"], "fts")
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["items"][0]["name"], "a.png")

    def test_search_multi_term_and(self):
        _mk(self.db, r"C:\pics\a.png", "a.png", "红色优惠券", "红色 优惠券")
        _mk(self.db, r"C:\pics\b.png", "b.png", "蓝色优惠券", "蓝色 优惠券")
        r = self.db.search("红色 优惠券")
        self.assertEqual(r["total"], 1)

    def test_search_like_fallback_single_char(self):
        _mk(self.db, r"C:\pics\a.png", "截图a.png", "内容没有特殊词", "内容 没有特殊词")
        r = self.db.search("单")  # 词表里没有"单"，应回退 LIKE
        self.assertEqual(r["mode"], "like")
        self.assertEqual(r["total"], 0)
        r2 = self.db.search("截图")
        self.assertGreaterEqual(r2["total"], 1)

    def test_search_like_fallback_covers_name(self):
        _mk(self.db, r"C:\pics\微信图片_2026.png", "微信图片_2026.png", "", "")
        r = self.db.search("微信图片")
        self.assertEqual(r["total"], 1)

    def test_purge_missing_scoped_to_roots(self):
        _mk(self.db, r"C:\pics\a.png", "a.png", "x", "x")
        _mk(self.db, r"D:\other\b.png", "b.png", "y", "y")
        n = self.db.purge_missing([r"C:\pics"], set())
        self.assertEqual(n, 1)
        st = self.db.stats()
        self.assertEqual(st["total"], 1)  # D:\other 不受影响

    def test_purge_keeps_existing(self):
        _mk(self.db, r"C:\pics\a.png", "a.png", "x", "x")
        n = self.db.purge_missing([r"C:\pics"], {r"c:\pics\a.png"})  # normcase 不敏感
        self.assertEqual(n, 0)

    def test_fingerprints(self):
        _mk(self.db, r"C:\pics\a.png", "a.png", "x", "x")
        fps = self.db.get_fingerprints()
        self.assertIn(r"C:\pics\a.png", fps)
        self.assertEqual(fps[r"C:\pics\a.png"], (10, 1000, 1))

    def test_get_path_by_id(self):
        i = _mk(self.db, r"C:\pics\a.png", "a.png", "x", "x")
        self.assertEqual(self.db.get_path_by_id(i), r"C:\pics\a.png")
        self.assertIsNone(self.db.get_path_by_id(999))

    def test_roots_and_meta(self):
        self.db.add_root(r"C:\pics")
        self.db.add_root(r"C:\pics")  # 重复无害
        self.assertEqual(self.db.list_roots(), [r"C:\pics"])
        self.db.set_meta("k", "v1")
        self.db.set_meta("k", "v2")
        self.assertEqual(self.db.get_meta("k"), "v2")
        self.assertEqual(self.db.get_meta("nope", "dft"), "dft")


if __name__ == "__main__":
    unittest.main()
