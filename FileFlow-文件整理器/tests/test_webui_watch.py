"""watch Web API 集成测试：真线程启停、事件轮询、错误分支。"""

import http.client
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fileflow import webui
from fileflow.rules import Rule, RuleEngine
from fileflow.scanner import Scanner

HOST = "127.0.0.1"


def req(port, method, path, body=None):
    conn = http.client.HTTPConnection(HOST, port, timeout=10)
    try:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


class WatchApiTest(unittest.TestCase):
    port = 8875

    @classmethod
    def setUpClass(cls):
        cls.logs_tmp = Path(tempfile.mkdtemp(prefix="ff-wapi-logs-"))
        from fileflow import executor
        cls._log_backup = executor.LOG_DIR
        executor.LOG_DIR = str(cls.logs_tmp)
        # 注入隔离的 watch 控制器（不读用户真实配置）
        engine = RuleEngine([Rule(name="图片", type="extension",
                                  target="图片", pattern="jpg")])
        cls.controller = webui.WatchController(engine, Scanner())
        webui._watch_ctrl = cls.controller
        cls.server = webui.make_server(HOST, cls.port)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        webui._watch_ctrl = None
        from fileflow import executor
        executor.LOG_DIR = cls._log_backup
        shutil.rmtree(cls.logs_tmp, ignore_errors=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ff-wapi-"))

    def tearDown(self):
        # 清理可能残留的监控线程
        try:
            self.controller.stop(str(self.root))
        except ValueError:
            pass
        shutil.rmtree(self.root, ignore_errors=True)

    def test_start_stop_roundtrip(self):
        status, st = req(self.port, "POST", "/api/watch/start",
                         {"directory": str(self.root), "interval": 1, "min_age": 0})
        self.assertEqual(status, 200)
        self.assertTrue(st["active"])
        self.assertEqual(st["interval"], 1)

        status, lst = req(self.port, "GET", "/api/watch")
        self.assertEqual(status, 200)
        self.assertEqual(len(lst["watches"]), 1)

        status, out = req(self.port, "POST", "/api/watch/stop",
                          {"directory": str(self.root)})
        self.assertEqual(status, 200)
        self.assertEqual(out["stopped"], str(self.root))
        self.assertFalse(out["stats"] is None)

        status, lst = req(self.port, "GET", "/api/watch")
        self.assertEqual(lst["watches"], [])

    def test_watch_moves_new_file_automatically(self):
        status, st = req(self.port, "POST", "/api/watch/start",
                         {"directory": str(self.root), "interval": 0.3, "min_age": 0})
        self.assertEqual(st["active"], True)

        p = self.root / "auto.jpg"
        p.write_text("photo", encoding="utf-8")
        # 等待后台线程处理（轮询最多 5 秒）
        deadline = time.time() + 5
        moved = False
        while time.time() < deadline:
            if (self.root / "图片" / "auto.jpg").exists():
                moved = True
                break
            time.sleep(0.2)
        self.assertTrue(moved, "后台线程未自动整理新文件")

        status, st = req(self.port, "GET", "/api/watch")
        w = st["watches"][0]
        # 计数器由后台线程在文件落位后自增（move 与计数非原子），
        # 高负载下可能读到旧值 —— 轮询等待，消除批跑时的假阴性
        deadline = time.time() + 5
        while time.time() < deadline and w["moved_total"] < 1:
            time.sleep(0.2)
            status, st = req(self.port, "GET", "/api/watch")
            w = st["watches"][0]
        self.assertGreaterEqual(w["cycles"], 1)
        self.assertGreaterEqual(w["moved_total"], 1)
        self.assertTrue(any("整理" in e for e in w["events"]))

    def test_start_invalid_dir_400(self):
        status, data = req(self.port, "POST", "/api/watch/start",
                           {"directory": str(self.root / "不存在")})
        self.assertEqual(status, 400)

    def test_stop_not_watching_400(self):
        status, data = req(self.port, "POST", "/api/watch/stop",
                           {"directory": str(self.root)})
        self.assertEqual(status, 400)

    def test_start_twice_is_idempotent(self):
        req(self.port, "POST", "/api/watch/start",
            {"directory": str(self.root), "interval": 1, "min_age": 0})
        status, st2 = req(self.port, "POST", "/api/watch/start",
                          {"directory": str(self.root), "interval": 1, "min_age": 0})
        self.assertEqual(status, 200)
        self.assertTrue(st2["active"])
        status, lst = req(self.port, "GET", "/api/watch")
        self.assertEqual(len(lst["watches"]), 1)   # 不重复起线程


if __name__ == "__main__":
    unittest.main()
