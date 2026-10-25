"""图片感知去重：回贴出去的图被重新采集（重编码字节不同、结构相同）→ 上浮不重插。"""

import hashlib
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from clipvault.clip import dhash_png
from clipvault.store import Store, _to_signed64, _to_unsigned64, perceptual_distance


def make_png(size=(40, 30), color=(13, 148, 138), variant: int = 0,
             level: int = 6) -> bytes:
    im = Image.new("RGB", size, color)
    if variant:
        # 垂直白条：dHash 比的是水平梯度，竖条才能改变它（横线整行同移不可见）
        px = im.load()
        for y in range(size[1]):
            px[size[0] // 4, y] = (255, 255, 255)
    buf = BytesIO()
    im.save(buf, "PNG", compress_level=level)
    return buf.getvalue()


class PerceptualTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.store = Store(tmp / "v.db", tmp / "img")
        # LIFO：store.close 必须在 tmp.cleanup 之后 add，才会先执行（Windows 文件占用）
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.store.close)

    def _capture_png(self, png: bytes) -> dict:
        return self.store.capture("image", "", image_path="a.png",
                                  size=len(png),
                                  chash=hashlib.sha256(png).hexdigest(),
                                  dhash=dhash_png(png))

    def test_dhash_stable_across_reencode(self):
        # 同一张图 PNG 重编码两次 → dHash 完全一致（结构不变）
        im = Image.new("RGB", (64, 48), (10, 200, 30))
        b1, b2 = BytesIO(), BytesIO()
        im.save(b1, "PNG", compress_level=1)
        im.save(b2, "PNG", compress_level=9)
        self.assertEqual(dhash_png(b1.getvalue()), dhash_png(b2.getvalue()))

    def test_dhash_differs_for_different_structure(self):
        self.assertNotEqual(dhash_png(make_png(variant=0)),
                            dhash_png(make_png(variant=1)))

    def test_reencoded_same_image_floats_old_entry(self):
        # 回采场景：字节不同（compress_level 9 vs 1）、像素相同 → 上浮旧条目，不新增
        first = self._capture_png(make_png(level=9))
        recoded = make_png(level=1)
        self.assertNotEqual(first["hash"], hashlib.sha256(recoded).hexdigest())
        second = self._capture_png(recoded)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.store.stats()["total"], 1)

    def test_different_image_within_window_stays_separate(self):
        first = self._capture_png(make_png(variant=0))
        second = self._capture_png(make_png(variant=1))
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.store.stats()["total"], 2)

    def test_similar_image_outside_window_not_merged(self):
        first = self._capture_png(make_png(level=9))
        # 把旧条目 ts 拨回窗口之外（模拟两小时前的记录）
        self.store.conn.execute("UPDATE entries SET ts=? WHERE id=?",
                                (time.time() - 7200, first["id"]))
        self.store.conn.commit()
        second = self._capture_png(make_png(level=1))  # 同结构、字节不同
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.store.stats()["total"], 2)

    def test_exact_hash_dedup_takes_priority(self):
        first = self._capture_png(make_png())
        again = self._capture_png(make_png())  # 字节级相同 → 精确去重
        self.assertEqual(first["id"], again["id"])

    def test_null_dhash_never_perceptually_merges(self):
        self.store.capture("image", "", image_path="a.png", size=1,
                           chash="ha1", dhash=None)
        self.store.capture("image", "", image_path="b.png", size=1,
                           chash="ha2", dhash=None)
        self.assertEqual(self.store.stats()["total"], 2)

    def test_signed64_roundtrip(self):
        for v in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1):
            self.assertEqual(_to_unsigned64(_to_signed64(v)), v)
        # 满哈希（全 1）入库后仍能正确读回
        self.store.capture("image", "", image_path="a.png", size=1,
                           chash="ha", dhash=(1 << 64) - 1)
        row = self.store.conn.execute(
            "SELECT dhash FROM entries WHERE id=1").fetchone()
        self.assertLess(row["dhash"], 1 << 63)  # 已按有符号存
        self.assertEqual(_to_unsigned64(row["dhash"]), (1 << 64) - 1)

    def test_perceptual_distance_symmetry(self):
        a, b = (1 << 64) - 1, 1 << 63
        self.assertEqual(perceptual_distance(a, b), perceptual_distance(b, a))
        self.assertEqual(perceptual_distance(a, a), 0)


if __name__ == "__main__":
    unittest.main()
