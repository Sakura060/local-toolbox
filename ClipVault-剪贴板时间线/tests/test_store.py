"""store：去重上浮、保留上限、置顶、搜索、暂停。全部用临时目录，不碰真实剪贴板。"""

import tempfile
import unittest
from pathlib import Path

from clipvault.store import Store, content_hash, kind_label


def make_store(tmp: str, retention: int = 100) -> Store:
    return Store(Path(tmp) / "vault.db", Path(tmp) / "images", retention=retention)


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)  # LIFO：后注册的 store.close 先跑
        self.store = make_store(self._tmp.name)

    def tearDown(self):
        self.store.close()

    def test_capture_and_list_order(self):
        self.store.capture("text", "first", chash=content_hash("first"))
        self.store.capture("text", "second", chash=content_hash("second"))
        rows = self.store.list()
        self.assertEqual([r["body"] for r in rows], ["second", "first"])
        self.assertEqual(rows[0]["kind"], "text")

    def test_dedup_moves_to_top(self):
        self.store.capture("text", "A", chash=content_hash("A"))
        self.store.capture("text", "B", chash=content_hash("B"))
        again = self.store.capture("text", "A", chash=content_hash("A"))
        rows = self.store.list()
        self.assertEqual([r["body"] for r in rows], ["A", "B"])
        self.assertEqual(rows[0]["id"], again["id"])  # 同一条上浮，不是新插
        self.assertEqual(len(rows), 2)

    def test_preview_by_kind(self):
        self.store.capture("text", "x" * 300, chash=content_hash("x300"))
        self.store.capture("files", r"C:\a\b.txt" + "\n" + r"C:\c\d.png",
                           chash=content_hash("two files"))
        rows = self.store.list()
        text_row = next(r for r in rows if r["kind"] == "text")
        files_row = next(r for r in rows if r["kind"] == "files")
        self.assertTrue(text_row["preview"].startswith("xxx"))
        self.assertLessEqual(len(text_row["preview"]), 160)
        self.assertIn("b.txt", files_row["preview"])
        self.assertIn("d.png", files_row["preview"])

    def test_search_and_kind_filter(self):
        self.store.capture("text", "运费多少", chash=content_hash("yf"))
        self.store.capture("text", "会议纪要", chash=content_hash("hy"))
        self.store.capture("files", r"C:\报告.docx", chash=content_hash("bg"))
        self.assertEqual(len(self.store.list(q="运费")), 1)
        self.assertEqual(len(self.store.list(kind="files")), 1)
        self.assertEqual(len(self.store.list(kind="text")), 2)

    def test_retention_prunes_unpinned_only(self):
        store = make_store(self._tmp.name, retention=5)
        self.addCleanup(store.close)
        ids = []
        for i in range(8):
            r = store.capture("text", f"item{i}", chash=content_hash(f"item{i}"))
            ids.append(r["id"])
        rows = store.list(limit=50)
        self.assertEqual(len([r for r in rows if not r["pinned"]]), 5)
        # 最老的被清掉
        self.assertNotIn(ids[0], [r["id"] for r in rows])

    def test_pin_survives_retention_and_clear(self):
        store = make_store(self._tmp.name, retention=3)
        self.addCleanup(store.close)
        pinned = store.capture("text", "keep me", chash=content_hash("keep"))
        store.pin(pinned["id"], True)
        for i in range(6):
            store.capture("text", f"noise{i}", chash=content_hash(f"noise{i}"))
        rows = store.list(limit=50)
        self.assertIn("keep me", [r["body"] for r in rows])
        n = store.clear(keep_pinned=True)
        self.assertEqual([r["body"] for r in store.list()], ["keep me"])
        self.assertGreaterEqual(n, 3)

    def test_pause_flag(self):
        self.assertFalse(self.store.stats()["paused"])
        self.store.set_paused(True)
        self.assertTrue(self.store.stats()["paused"])
        self.store.set_paused(False)
        self.assertFalse(self.store.stats()["paused"])

    def test_delete_removes_image_file(self):
        img_dir = self.store.images_dir
        f = img_dir / "test.png"
        f.write_bytes(b"\x89PNG fake")
        entry = self.store.capture("image", "", image_path="test.png",
                                   size=10, chash=content_hash(b"\x89PNG fake"))
        self.assertTrue(f.exists())
        self.assertTrue(self.store.delete(entry["id"]))
        self.assertFalse(f.exists())
        self.assertIsNone(self.store.get(entry["id"]))

    def test_kind_label(self):
        self.assertEqual(kind_label("text"), "文本")
        self.assertEqual(kind_label("image"), "图片")
        self.assertEqual(kind_label("files"), "文件")

    def test_limit_garbage_clamps(self):
        self.store.capture("text", "s", chash=content_hash("s1"))
        rows = self.store.list(limit="abc")  # 曾直接 ValueError 炸连接
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(self.store.list(limit=-5)), 1)
        self.assertEqual(len(self.store.list(limit=10 ** 9)), 1)

    def test_search_escapes_like_wildcards(self):
        self.store.capture("text", "a_100", chash=content_hash("u1"))
        self.store.capture("text", "ab100", chash=content_hash("u2"))
        self.store.capture("text", "增长100%", chash=content_hash("u3"))
        # 不转义时 a_100 会用 _ 通配误命中 ab100
        self.assertEqual([r["body"] for r in self.store.list(q="a_100")], ["a_100"])
        # 不转义时 "100%" 会把 % 当通配符放大匹配
        self.assertEqual([r["body"] for r in self.store.list(q="100%")], ["增长100%"])

    def test_list_body_truncated_full_via_get(self):
        big = "x" * 6000
        entry = self.store.capture("text", big, chash=content_hash(big))
        rows = self.store.list()
        self.assertLessEqual(len(rows[0]["body"]), 4000)
        self.assertTrue(rows[0].get("body_truncated"))
        self.assertEqual(len(self.store.get(entry["id"])["body"]), 6000)  # 全文仍在

    def test_empty_hash_never_merges(self):
        # 反向检修：空 hash 若走去重会把不同内容误合并成一条
        a = self.store.capture("text", "内容甲", chash="")
        b = self.store.capture("text", "内容乙", chash="")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self.store.list()), 2)


if __name__ == "__main__":
    unittest.main()
