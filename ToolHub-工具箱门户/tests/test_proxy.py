"""proxy：fetch 透传 / probe 探测 / UpstreamError 分类。"""

import json
import unittest

from toolhub import proxy
from tests.helpers import dead_port, make_fake_upstream


class TestFetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def responder(path):
            if path == "/nope":
                return (404, {"error": "not found"})
            return (200, {"hello": "world", "path": path})

        cls.srv, cls.port = make_fake_upstream(responder)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_fetch_json_roundtrip(self):
        res = proxy.fetch(self.port, "/api/thing?a=1")
        self.assertEqual(res.status, 200)
        self.assertIn("application/json", res.content_type)
        data = json.loads(res.body.decode("utf-8"))
        self.assertEqual(data["hello"], "world")
        self.assertEqual(data["path"], "/api/thing?a=1")

    def test_fetch_rejects_bad_path(self):
        with self.assertRaises(proxy.UpstreamError):
            proxy.fetch(self.port, "api/thing")

    def test_fetch_upstream_404_passthrough(self):
        res = proxy.fetch(self.port, "/nope")
        self.assertEqual(res.status, 404)

    def test_fetch_dead_port_raises_upstream_error(self):
        with self.assertRaises(proxy.UpstreamError):
            proxy.fetch(dead_port(), "/")

    def test_fetch_max_bytes_truncation(self):
        res = proxy.fetch(self.port, "/big", max_bytes=8)
        self.assertEqual(len(res.body), 8)
        self.assertTrue(res.truncated)


class TestProbe(unittest.TestCase):
    def test_online(self):
        srv, port = make_fake_upstream(lambda path: (200, "ok"))
        try:
            online, ms = proxy.probe(port)
            self.assertTrue(online)
            self.assertGreaterEqual(ms, 0)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_offline_dead_port(self):
        online, ms = proxy.probe(dead_port(), timeout=0.3)
        self.assertFalse(online)
        self.assertEqual(ms, 0)


if __name__ == "__main__":
    unittest.main()
