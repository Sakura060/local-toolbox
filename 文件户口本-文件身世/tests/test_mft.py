"""mft 纯函数 + deep 快照存储 + /api/lineage 端到端测试（合成数据，零系统调用）。"""

import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from hukou import mft
from hukou.db import Database
from hukou.server import make_server


def REC(frn, pfrn, name, usn=0, ts=0.0, reason=0):
    return {"usn": usn, "frn": frn, "pfrn": pfrn, "ts": ts,
            "reason": reason, "name": name}


# C:\ → Users(frn 100) → t(101) → file.txt(102)
TREE = [
    REC(100, 5, "Users"),
    REC(101, 100, "t"),
    REC(102, 101, "file.txt"),
    REC(200, 5, "Windows"),
    REC(201, 200, "System32"),
]


class TestPathResolve(unittest.TestCase):
    def setUp(self):
        self.idx = mft.build_parent_index(TREE)

    def test_resolve_simple(self):
        self.assertEqual(mft.resolve_path(self.idx, "C", 102),
                         r"C:\Users\t\file.txt")
        self.assertEqual(mft.resolve_path(self.idx, "C", 201),
                         r"C:\Windows\System32")

    def test_resolve_root(self):
        self.assertEqual(mft.resolve_path(self.idx, "C", mft.ROOT_FRN), "C:\\")

    def test_orphan_returns_none(self):
        orphan = REC(300, 999, "ghost.txt")
        idx = mft.build_parent_index(TREE + [orphan])
        self.assertIsNone(mft.resolve_path(idx, "C", 300))

    def test_cycle_returns_none(self):
        cyc = [REC(400, 401, "a"), REC(401, 400, "b")]
        idx = mft.build_parent_index(TREE + cyc)
        self.assertIsNone(mft.resolve_path(idx, "C", 400))

    def test_path_map_resolves_all_and_skips_orphans(self):
        orphan = REC(300, 999, "ghost.txt")
        pmap = mft.build_path_map(TREE + [orphan], "C")
        self.assertEqual(pmap[102], r"C:\Users\t\file.txt")
        self.assertNotIn(300, pmap)

    def test_name_collision_same_name_ok(self):
        # 不同目录下同名文件互不干扰
        extra = REC(103, 200, "file.txt")
        pmap = mft.build_path_map(TREE + [extra], "C")
        self.assertEqual(pmap[102], r"C:\Users\t\file.txt")
        self.assertEqual(pmap[103], r"C:\Windows\file.txt")


class TestLineage(unittest.TestCase):
    def test_full_chain(self):
        events = [
            {"usn": 10, "ts": 1.0, "frn": 102, "pfrn": 101,
             "name": "file.txt", "reason": 0x100},          # 新建
            {"usn": 20, "ts": 2.0, "frn": 102, "pfrn": 101,
             "name": "file.txt", "reason": 0x2},            # 数据扩展：不进谱系
            {"usn": 30, "ts": 3.0, "frn": 102, "pfrn": 101,
             "name": "renamed.txt", "reason": 0x2000},      # 改名
            {"usn": 40, "ts": 4.0, "frn": 102, "pfrn": 200,
             "name": "renamed.txt", "reason": 0x2000},      # 移动（pfrn 变了）
            {"usn": 50, "ts": 5.0, "frn": 102, "pfrn": 200,
             "name": "renamed.txt", "reason": 0x200},       # 删除
        ]
        chain = mft.lineage_from_events(events)
        self.assertEqual([c["action"] for c in chain],
                         ["新建", "改名", "移动", "删除"])
        self.assertEqual(chain[2]["name"], "renamed.txt")

    def test_unsorted_input_sorted_by_usn(self):
        events = [
            {"usn": 30, "ts": 3.0, "frn": 1, "pfrn": 2,
             "name": "b.txt", "reason": 0x2000},
            {"usn": 10, "ts": 1.0, "frn": 1, "pfrn": 2,
             "name": "a.txt", "reason": 0x100},
        ]
        chain = mft.lineage_from_events(events)
        self.assertEqual([c["name"] for c in chain], ["a.txt", "b.txt"])


class TestDbFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_store_and_resolve(self):
        n = self.db.store_files("C", TREE)
        self.assertEqual(n, len(TREE))
        self.assertEqual(self.db.count_files("C"), len(TREE))
        idx = self.db.frn_index("C")
        self.assertEqual(mft.resolve_path(idx, "C", 102), r"C:\Users\t\file.txt")
        # 幂等：重存不翻倍
        self.db.store_files("C", TREE)
        self.assertEqual(self.db.count_files("C"), len(TREE))

    def test_find_child_walk(self):
        self.db.store_files("C", TREE)
        f = self.db.find_child("C", 5, "Users")
        f = self.db.find_child("C", f, "t")
        f = self.db.find_child("C", f, "file.txt")
        self.assertEqual(f, 102)
        self.assertIsNone(self.db.find_child("C", 5, "不存在的目录"))

    def test_meta_and_events(self):
        self.db.store_files("C", TREE)
        self.db.set_meta("snapshot_C", "123|4")
        self.assertEqual(self.db.get_meta("snapshot_C"), "123|4")
        self.db.add_events("C", [
            {"usn": 10, "ts": 1.0, "frn": 102, "pfrn": 101,
             "name": "file.txt", "reason": "256"},
        ])
        ev = self.db.events_for_frn("C", 102)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["name"], "file.txt")


class TestLineageEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.db.store_files("C", TREE)
        self.db.add_events("C", [
            {"usn": 10, "ts": 1767000000.0, "frn": 102, "pfrn": 101,
             "name": "file.txt", "reason": "256"},
            {"usn": 30, "ts": 1767000060.0, "frn": 102, "pfrn": 101,
             "name": "renamed.txt", "reason": "8192"},
        ])
        self.server = make_server(0, self.db)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

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

    def test_lineage_hit(self):
        q = urllib.parse.quote(r"C:\Users\t\file.txt")
        status, body = self._get(f"/api/lineage?q={q}")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["frn"], 102)
        self.assertEqual([c["action"] for c in data["chain"]], ["新建", "改名"])

    def test_lineage_no_snapshot(self):
        q = urllib.parse.quote(r"D:\somewhere\x.txt")
        _, body = self._get(f"/api/lineage?q={q}")
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data["ok"])

    def test_lineage_path_not_in_snapshot(self):
        q = urllib.parse.quote(r"C:\Users\t\ghost.txt")
        _, body = self._get(f"/api/lineage?q={q}")
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data["ok"])

    def test_lineage_missing_q(self):
        status, _ = self._get("/api/lineage")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
