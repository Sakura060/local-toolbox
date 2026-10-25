"""USN 纯函数测试：合成 buffer 往返解析。"""

import struct
import unittest

from hukou import usn


def build_record(usn=1, frn=100, pfrn=200, ts_ft=132000000000000000,
                 reason=0x100, name="hello.txt"):
    name_bytes = name.encode("utf-16-le")
    rec_len = 60 + len(name_bytes)
    buf = struct.pack("<IHH", rec_len, 2, 0)
    buf += struct.pack("<QQQ", frn, pfrn, usn)
    buf += struct.pack("<QIIII", ts_ft, reason, 0, 0, 0x20)
    buf += struct.pack("<HH", len(name_bytes), 60)
    buf += name_bytes
    return buf


def build_page(records):
    return struct.pack("<Q", 999999) + b"".join(records)  # 8 字节游标头 + 记录


class TestUsnParse(unittest.TestCase):
    def test_parse_basic(self):
        page = build_page([build_record(usn=42, frn=100, pfrn=55,
                                        reason=0x100, name="a.txt")])
        recs = usn.parse_records(page)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["usn"], 42)
        self.assertEqual(r["frn"], 100)
        self.assertEqual(r["pfrn"], 55)
        self.assertEqual(r["name"], "a.txt")
        self.assertEqual(r["reason"], 0x100)

    def test_parse_unicode_name(self):
        page = build_page([build_record(name="报销单2026.xlsx"),
                           build_record(usn=2, name="微信图片.png")])
        recs = usn.parse_records(page)
        self.assertEqual([r["name"] for r in recs],
                         ["报销单2026.xlsx", "微信图片.png"])

    def test_parse_truncated_tail_stops_cleanly(self):
        page = build_page([build_record()])
        bad = page[: len(page) - 3]  # 截断尾部
        recs = usn.parse_records(bad)
        self.assertEqual(len(recs), 0)  # 截断在记录中间 → 该记录不可用，不抛

    def test_parse_garbage(self):
        self.assertEqual(usn.parse_records(b"\x00" * 64), [])
        self.assertEqual(usn.parse_records(b""), [])

    def test_multiple_records_stride(self):
        page = build_page([build_record(usn=i, name=f"f{i}.txt") for i in range(5)])
        recs = usn.parse_records(page)
        self.assertEqual([r["usn"] for r in recs], [0, 1, 2, 3, 4])

    def test_filetime(self):
        # 2026-01-01 UTC = (1767225600 + 11644473600) × 1e7 FILETIME
        ft = 134116992000000000
        self.assertAlmostEqual(usn.filetime_to_unix(ft), 1767225600.0, delta=1)
        self.assertEqual(usn.filetime_to_unix(0), 0.0)

    def test_reason_names(self):
        names = usn.reason_names(0x1000 | 0x100)
        self.assertIn("新建", names)
        self.assertIn("改名(旧名)", names)
        self.assertNotIn("删除", names)
        self.assertEqual(usn.reason_names(0), [])

    def test_interesting_mask(self):
        self.assertTrue(0x2000 & usn.INTERESTING_REASONS)   # 改名新名
        self.assertTrue(0x200 & usn.INTERESTING_REASONS)    # 删除
        self.assertFalse(0x1 & usn.INTERESTING_REASONS)     # 数据覆盖不关注


if __name__ == "__main__":
    unittest.main()
