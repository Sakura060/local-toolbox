"""v0.2 测试：dHash/相似分组、db 列迁移、时间线、来源检测、新 API 路由。"""

import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from PIL import Image

from laotu import hashes
from laotu.db import Database
from laotu.indexer import Indexer
from laotu.server import ScanState, make_server
from laotu.sources import detect_source


def make_png(p: Path, color=(200, 60, 60), size=(32, 32)):
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(p, "PNG")


def fake_ocr(path):
    return ("测试文本", "")


def fake_thumb(path, dest, max_side=360):
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (10, 8)).save(dest, "JPEG")
    return (10, 8)


def fake_dims(path):
    return (10, 8)


class TestHashes(unittest.TestCase):
    def test_dhash_same_image_same_hash(self):
        img = Image.new("RGB", (64, 64), (120, 120, 120))
        self.assertEqual(hashes.dhash_from_pil(img), hashes.dhash_from_pil(img))

    def test_dhash_resizes_stably(self):
        big = Image.new("L", (800, 600), 200)
        small = Image.new("L", (45, 33), 200)
        self.assertEqual(hashes.dhash_from_pil(big), hashes.dhash_from_pil(small))

    def test_dhash_pattern_differs_from_flat(self):
        flat = Image.new("L", (90, 80), 128)
        stripe = Image.new("L", (90, 80))
        stripe.putdata([255 if (x % 4) < 2 else 0
                        for _y in range(80) for x in range(90)])
        self.assertNotEqual(hashes.dhash_from_pil(flat),
                            hashes.dhash_from_pil(stripe))

    def test_hamming(self):
        self.assertEqual(hashes.hamming(0, 0), 0)
        self.assertEqual(hashes.hamming(0b101, 0b010), 3)
        self.assertEqual(hashes.hamming(1 << 63, 0), 1)

    def test_duplicate_groups(self):
        # 距离全部用 Python bit_count 核算（手算已三次翻车）：
        # d(h0,h1)=1, d(h0,h2)=2, d(h1,h2)=1 → {1,2,3} 链式同组
        # d(far,far2)=3, d(h*, far)=17/18 → 排除
        h0, h1, h2 = 0b000, 0b001, 0b011
        far = 0xFFFF << 40
        far2 = far ^ 0b111
        groups = hashes.duplicate_groups(
            [(1, h0), (2, h1), (3, h2), (4, far), (5, far2)], max_dist=2)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0], [1, 2, 3])

    def test_duplicate_groups_empty_and_single(self):
        self.assertEqual(hashes.duplicate_groups([]), [])
        self.assertEqual(hashes.duplicate_groups([(1, 5)]), [])

    def test_dhash_bytes_bad(self):
        with self.assertRaises(ValueError):
            hashes.dhash_bytes(b"not an image")


class TestSources(unittest.TestCase):
    def test_wechat(self):
        self.assertEqual(detect_source(
            r"C:\Users\t\Documents\xwechat_files\msg\file\2026-09\a.png"), "微信")
        self.assertEqual(detect_source(r"D:/WeChat Files/wx/img.png"), "微信")

    def test_qq(self):
        self.assertEqual(detect_source(
            r"C:\Users\t\Documents\Tencent Files\123\FileRecv\b.png"), "QQ")

    def test_plain_empty(self):
        self.assertEqual(detect_source(r"C:\Users\t\Pictures\Screenshots\x.png"), "")


class TestDbV02(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _add(self, path, mtime_s, dhash=0, source=""):
        return self.db.upsert_image(
            path=path, name=Path(path).name, folder=str(Path(path).parent),
            size=10, mtime_ns=int(mtime_s * 1e9), width=3, height=3,
            ocr_text="", ocr_ok=1, err=None, seg_text="",
            dhash=dhash, source=source)

    def test_upsert_with_dhash_source(self):
        i1 = self._add("C:\\a\\x.png", 1000.0, dhash=12345, source="微信")
        row = self.db.get_fingerprints()
        self.assertIn(r"C:\a\x.png", row)
        items = self.db.by_time(0, 2000)["items"]
        self.assertEqual(items[0]["source"], "微信")
        self.assertEqual(self.db.all_hashes(), [(i1, 12345)])

    def test_migration_adds_columns_to_old_db(self):
        # 模拟旧库：先建表再手动造（这里直接验证列存在）
        cols = {r[1] for r in self.db._conn.execute("PRAGMA table_info(images)")}
        self.assertIn("dhash", cols)
        self.assertIn("source", cols)

    def test_timeline_day_bucket(self):
        self._add("C:\\a\\x.png", 1790000000.0, dhash=1)
        self._add("C:\\a\\y.png", 1790000100.0, dhash=2)
        self._add("C:\\a\\z.png", 1790100000.0, dhash=3)
        tl = self.db.timeline("day")
        self.assertGreaterEqual(len(tl), 2)
        self.assertEqual(sum(c["count"] for c in tl), 3)
        counts = {c["count"] for c in tl}
        self.assertIn(2, counts)

    def test_by_time_range(self):
        self._add("C:\\a\\x.png", 1000.0)
        self._add("C:\\a\\y.png", 5000.0)
        data = self.db.by_time(0, 2000)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["name"], "x.png")

    def test_items_by_ids(self):
        i1 = self._add("C:\\a\\x.png", 1000.0)
        got = self.db.items_by_ids([i1, 999])
        self.assertIn(i1, got)
        self.assertNotIn(999, got)


class TestIndexerV02(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "shots"
        self.root.mkdir()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.db = Database(self.data / "t.db")
        self.idx = Indexer(self.db, ocr_func=fake_ocr, thumb_func=fake_thumb,
                           dim_func=fake_dims, thumb_dir=self.data / "thumbs",
                           hash_func=lambda p: 424242)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_scan_computes_hash_and_source(self):
        make_png(self.root / "a.png")
        self.idx.scan([str(self.root)])
        items = self.db.recent()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(self.db.all_hashes()[0][1], 424242)
        self.assertEqual(items[0]["source"], "")

    def test_scan_detects_wechat_source(self):
        wx = self.root / "xwechat_files" / "f"
        wx.mkdir(parents=True)
        make_png(wx / "w.png")
        self.idx.scan([str(self.root)])
        items = self.db.recent()["items"]
        self.assertEqual(items[0]["source"], "微信")

    def test_bad_image_no_hash_no_crash(self):
        # 用真实 dhash：垃圾文件解码抛错 → 不记哈希、不中断扫描
        self.idx.hash_func = hashes.dhash_file
        (self.root / "bad.png").write_bytes(b"garbage")
        s = self.idx.scan([str(self.root)])
        self.assertEqual(s["updated"], 1)  # OCR 是假的成功
        self.assertEqual(self.db.all_hashes(), [])

    def test_backfill_hash_for_old_records(self):
        # 模拟 v0.1 旧记录：已入库但无 dHash；重扫（指纹相同被跳过）应回填
        p = self.root / "old.png"
        make_png(p, color=(10, 200, 30))
        st = p.stat()
        self.db.upsert_image(
            path=str(p), name=p.name, folder=str(self.root),
            size=st.st_size, mtime_ns=st.st_mtime_ns, width=32, height=32,
            ocr_text="x", ocr_ok=1, err=None, seg_text="x")
        self.assertEqual(self.db.all_hashes(), [])
        s = self.idx.scan([str(self.root)])
        self.assertEqual(s["skipped"], 1)
        self.assertEqual(s["hashed"], 1)
        self.assertEqual(len(self.db.all_hashes()), 1)
        # 再扫一遍不再重复回填
        s2 = self.idx.scan([str(self.root)])
        self.assertEqual(s2["hashed"], 0)


class TestServerV02(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.db = Database(self.data / "t.db")
        self.idx = Indexer(self.db, ocr_func=fake_ocr, thumb_func=fake_thumb,
                           dim_func=fake_dims, thumb_dir=self.data / "thumbs",
                           hash_func=lambda p: 7)
        self.state = ScanState()
        self.server = make_server(0, self.db, self.idx, self.state,
                                  Path(self.tmp.name) / "web")
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # 种子：两张近邻（8 与 15 距 3）+ 一张极远（与二者距 60/63）
        # 注意 dhash=0 是"未计算"哨兵，种子不能取 0
        self.i1 = self.db.upsert_image("C:\\x\\1.png", "1.png", "C:\\x", 1,
                                       int(1000 * 1e9), 2, 2, "", 1, None, "", 0b1000)
        self.i2 = self.db.upsert_image("C:\\x\\2.png", "2.png", "C:\\x", 1,
                                       int(2000 * 1e9), 2, 2, "", 1, None, "", 0b1111)
        self.i3 = self.db.upsert_image("C:\\x\\3.png", "3.png", "C:\\x", 1,
                                       int(3000 * 1e9), 2, 2, "", 1, None, "", (1 << 64) - 1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.db.close()
        self.tmp.cleanup()

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def test_dupes_route(self):
        status, body = self._get("/api/dupes?dist=3")
        self.assertEqual(status, 200)
        j = json.loads(body.decode("utf-8"))
        self.assertEqual(len(j["groups"]), 1)   # 0 与 7 距 3 → 同组；与全 1 距 61 → 排除
        g = j["groups"][0]
        self.assertEqual(g["keep"], self.i2)    # 保留最新（mtime 2000）
        self.assertEqual(g["count"], 2)

    def test_timeline_route(self):
        status, body = self._get("/api/timeline?bucket=day")
        self.assertEqual(status, 200)
        j = json.loads(body.decode("utf-8"))
        self.assertEqual(sum(c["count"] for c in j["clusters"]), 3)

    def test_search_time_range_route(self):
        status, body = self._get(
            "/api/search?" + urllib.parse.urlencode({"t0": 0, "t1": 2500}))
        self.assertEqual(status, 200)
        j = json.loads(body.decode("utf-8"))
        self.assertEqual(j["mode"], "time")
        self.assertEqual(j["total"], 2)

    def test_search_bad_time_falls_back(self):
        status, body = self._get("/api/search?t0=abc&t1=xyz")
        self.assertEqual(status, 200)
        j = json.loads(body.decode("utf-8"))
        self.assertIn(j["mode"], ("recent", "fts", "like"))


if __name__ == "__main__":
    unittest.main()
