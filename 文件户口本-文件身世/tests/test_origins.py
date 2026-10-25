"""来源归因测试：ADS / 浏览器库 / 目录启发式 / DB 优先级。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from hukou import ads, browsers, classify
from hukou.db import Database


class TestAds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "file.txt"
        self.p.write_text("hello", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_zone_text(self):
        text = "[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://e.com/p\r\nHostUrl=https://e.com/f.exe\r\n"
        d = ads.parse_zone_text(text)
        self.assertEqual(d["ZoneId"], "3")
        self.assertEqual(d["HostUrl"], "https://e.com/f.exe")

    def test_read_real_ads(self):
        ads_path = Path(f"{self.p}:Zone.Identifier")
        ads_path.write_text(
            "[ZoneTransfer]\nZoneId=3\nHostUrl=https://example.com/x.zip\n",
            encoding="utf-8",
        )
        zone = ads.read_zone_identifier(self.p)
        self.assertIsNotNone(zone)
        self.assertEqual(zone["HostUrl"], "https://example.com/x.zip")
        src, detail = ads.origin_from_zone(zone)
        self.assertEqual(src, "网络下载")
        self.assertIn("example.com", detail)

    def test_no_ads_returns_none(self):
        self.assertIsNone(ads.read_zone_identifier(self.p))

    def test_missing_file_returns_none(self):
        self.assertIsNone(ads.read_zone_identifier(r"Z:\nope\nada.bin"))


class TestBrowsers(unittest.TestCase):
    def test_chrome_time(self):
        # 2024-01-01 UTC = (1704067200 + 11644473600) × 1e6 微秒
        ts = browsers.chrome_time_to_unix(13348540800000000)
        self.assertAlmostEqual(ts, 1704067200.0, delta=2)
        self.assertEqual(browsers.chrome_time_to_unix(0), 0.0)

    def test_query_downloads_from_fake_history(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "History"
            conn = sqlite3.connect(src)
            conn.executescript(
                """
                CREATE TABLE downloads(id INTEGER PRIMARY KEY, target_path TEXT,
                  tab_url TEXT, start_time INTEGER, received_bytes INTEGER);
                INSERT INTO downloads(target_path, tab_url, start_time, received_bytes)
                VALUES('C:\\Users\\t\\Downloads\\setup.exe',
                       'https://example.com/setup', 13348540800000000, 1024);
                """
            )
            conn.commit()
            conn.close()
            recs = browsers.query_downloads(src, Path(td))
            self.assertEqual(len(recs), 1)
            r = recs[0]
            self.assertEqual(r["path"], "C:\\Users\\t\\Downloads\\setup.exe")
            self.assertIn("example.com", r["url"])
            self.assertAlmostEqual(r["time"], 1704067200.0, delta=2)

    def test_query_downloads_bad_db(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "History"
            bad.write_bytes(b"not sqlite")
            self.assertEqual(browsers.query_downloads(bad, Path(td)), [])


class TestClassify(unittest.TestCase):
    def test_wechat_new(self):
        g = classify.guess_origin(
            r"C:\Users\t\Documents\xwechat_files\wxid_1\msg\file\2026-09\doc.pdf"
        )
        self.assertEqual(g[0], "微信接收")

    def test_wechat_old(self):
        g = classify.guess_origin(
            r"C:\Users\t\Documents\WeChat Files\wxid_1\FileStorage\File\2026-09\a.zip"
        )
        self.assertEqual(g[0], "微信接收")

    def test_qq_recv(self):
        g = classify.guess_origin(r"D:\Tencent Files\12345\FileRecv\图.png")
        self.assertEqual(g[0], "QQ接收")

    def test_no_false_positive_substring(self):
        self.assertIsNone(classify.guess_origin(r"C:\Users\t\MyDownloads\a.exe"))
        self.assertIsNone(classify.guess_origin(r"C:\work\a.exe"))

    def test_downloads_dir(self):
        g = classify.guess_origin(r"C:\Users\t\Downloads\a.exe")
        self.assertEqual(g[0], "浏览器下载")


class TestDbPriority(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_higher_confidence_wins(self):
        path = r"C:\Users\t\Downloads\setup.exe"
        self.assertTrue(self.db.upsert_origin(path, "浏览器下载", "目录推测", "dir"))
        self.assertFalse(self.db.upsert_origin(path, "浏览器下载", "另一条目录推测", "dir"))
        r = self.db.lookup_origin(path)
        self.assertEqual(r["confidence"], "dir")
        self.assertTrue(self.db.upsert_origin(path, "网络下载", "https://x", "ads"))
        r = self.db.lookup_origin(path)
        self.assertEqual(r["confidence"], "ads")
        self.assertEqual(r["detail"], "https://x")

    def test_events(self):
        n = self.db.add_events("C", [
            {"usn": 1, "ts": 1.0, "frn": 1, "pfrn": 2, "name": "a.txt", "reason": "256"},
            {"usn": 1, "ts": 2.0, "frn": 1, "pfrn": 2, "name": "a.txt", "reason": "256"},  # 重复
            {"usn": 2, "ts": 3.0, "frn": 1, "pfrn": 2, "name": "a.txt", "reason": "4096"},
        ])
        self.assertEqual(n, 2)
        evs = self.db.events_for_name("a.txt")
        self.assertEqual(len(evs), 2)

    def test_cursor_roundtrip(self):
        self.assertEqual(self.db.get_usn_cursor("C"), 0)
        self.db.set_usn_cursor("C", 12345)
        self.assertEqual(self.db.get_usn_cursor("C"), 12345)

    def test_search_origins(self):
        self.db.upsert_origin(r"C:\Users\t\Downloads\报销单.xlsx", "网络下载", "u", "ads")
        rows = self.db.search_origins("报销")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["src_type"], "网络下载")


if __name__ == "__main__":
    unittest.main()
