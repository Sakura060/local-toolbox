"""mdns 纯函数 + 同进程 announce/browse 集成测试（沙箱可跑；跨进程真机验证留用户）。"""

import socket
import struct
import time
import unittest

from landrop import mdns


class TestNames(unittest.TestCase):
    def test_encode_simple(self):
        self.assertEqual(mdns.encode_name("a.b.local"),
                         b"\x01a\x01b\x05local\x00")

    def test_encode_escaped_dot(self):
        self.assertEqual(mdns.encode_name(r"my\.pc.local"),
                         b"\x05my.pc\x05local\x00")

    def test_encode_root_and_empty(self):
        self.assertEqual(mdns.encode_name("."), b"\x00")
        self.assertEqual(mdns.encode_name(""), b"\x00")

    def test_roundtrip(self):
        for name in ("_landrop._tcp.local", r"my\.pc._landrop._tcp.local",
                     "x"):
            buf = mdns.encode_name(name)
            out, off = mdns.decode_name(buf, 0)
            self.assertEqual(out, name + ".")
            self.assertEqual(off, len(buf))

    def test_decode_compression_pointer(self):
        # 名字在偏移 0；后面一条 RR 名字用指针 0xC000 引用它
        target = mdns.encode_name("pc._landrop._tcp.local")
        buf = target + b"\xc0\x00" + struct.pack(">HHIH", 33, 1, 120, 6) \
            + struct.pack(">HHH", 0, 0, 9000) + b"\x00"
        name, off = mdns.decode_name(buf, len(target))
        self.assertEqual(name, "pc._landrop._tcp.local.")
        self.assertEqual(off, len(target) + 2)

    def test_decode_pointer_loop_guard(self):
        with self.assertRaises(ValueError):
            mdns.decode_name(b"\xc0\x00", 0)


class TestPackets(unittest.TestCase):
    def test_query_shape(self):
        pkt = mdns.parse_packet(mdns.build_query())
        self.assertFalse(pkt["is_response"])
        self.assertEqual(len(pkt["questions"]), 1)
        self.assertEqual(pkt["questions"][0]["name"], mdns.SERVICE_TYPE + ".")
        self.assertEqual(pkt["questions"][0]["type"], mdns.TYPE_PTR)

    def test_response_roundtrip(self):
        raw = mdns.build_response("测试机", 9000, {"dv": "landrop-lite"},
                                  "192.0.2.50", "testhost")
        pkt = mdns.parse_packet(raw)
        self.assertTrue(pkt["is_response"])
        types = {a["type"] for a in pkt["answers"]}
        self.assertEqual(types, {mdns.TYPE_PTR, mdns.TYPE_SRV,
                                 mdns.TYPE_TXT, mdns.TYPE_A})
        peer = mdns.peers_from_packet(pkt)
        self.assertIsNotNone(peer)
        self.assertEqual(peer["instance"], "测试机")
        self.assertEqual(peer["port"], 9000)
        self.assertEqual(peer["ip"], "192.0.2.50")
        self.assertEqual(peer["hostname"], "testhost.local.")
        self.assertEqual(peer["txt"]["dv"], "landrop-lite")

    def test_peers_rejects_query_and_incomplete(self):
        self.assertIsNone(mdns.peers_from_packet(mdns.parse_packet(
            mdns.build_query())))
        # 只发 TXT 一条（缺 SRV/A）→ None
        raw = (struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0)
               + mdns._rr(mdns.SERVICE_TYPE, mdns.TYPE_TXT, b"\x00"))
        self.assertIsNone(mdns.peers_from_packet(mdns.parse_packet(raw)))

    def test_parse_garbage_never_raises(self):
        for blob in (b"", b"\x00", b"\xff" * 11, b"\xde\xad\xbe\xef" * 8,
                     mdns.build_response("x", 1, {}, "1.2.3.4", "h")[:20]):
            mdns.parse_packet(blob)  # 不抛即通过


class TestSameProcessDiscovery(unittest.TestCase):
    """announce + browse 同进程集成（probe 实证：同进程组播回环可用）。"""

    def setUp(self):
        # 避开 5353（可能被系统 mDNS 栈占用/防火墙干预），用探针端口
        self._orig_port = mdns.MDNS_PORT
        mdns.MDNS_PORT = 55353
        self.ann = mdns.Announcer("集成测试机", 9000, {"dv": "landrop-lite"},
                                  "192.0.2.50", "itest")
        self.browser = mdns.Browser()
        assert self.ann.start(), "announcer bind failed"
        assert self.browser.start(), "browser bind failed"

    def tearDown(self):
        self.ann.stop()
        self.browser.stop()
        mdns.MDNS_PORT = self._orig_port

    def test_browser_discovers_announcer(self):
        deadline = time.monotonic() + 8
        found = []
        while time.monotonic() < deadline:
            self.browser.refresh()
            found = [p for p in self.browser.peers() if p["instance"] == "集成测试机"]
            if found:
                break
            time.sleep(0.4)
        self.assertTrue(found, "browser did not discover announcer in-process")
        self.assertEqual(found[0]["port"], 9000)
        self.assertEqual(found[0]["txt"]["dv"], "landrop-lite")

    def test_peers_cache_expiry(self):
        peer = {"instance": "ghost", "ip": "10.0.0.1", "port": 1,
                "hostname": "g.local.", "txt": {}, "_seen": time.monotonic() - 999}
        with self.browser._lock:
            self.browser._peers["ghost"] = peer
        self.assertEqual([p for p in self.browser.peers() if p["instance"] == "ghost"], [])


if __name__ == "__main__":
    unittest.main()
