"""server：路由、状态并行探测、代理白名单、启动注入。"""

import http.client
import json
import tempfile
import unittest
from pathlib import Path

from toolhub.server import make_server, probe_all
from toolhub.registry import WORKSPACE_ROOT, Tool
from tests.helpers import dead_port, make_fake_upstream


def make_tool(tid: str, port: int, bat: str = "", proxy_prefixes=(),
              search: bool = False) -> Tool:
    return Tool(id=tid, name=tid, desc="", port=port, bat=bat, search=search,
                proxy=proxy_prefixes)


def http_req(port: int, path: str, method: str = "GET", body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, data
    finally:
        conn.close()


class ServerTestBase(unittest.TestCase):
    """起一个注入假工具/假启动器的门户实例。"""

    @classmethod
    def setUpClass(cls):
        cls.up_srv, cls.up_port = make_fake_upstream(
            lambda path: (200, {"items": [{"id": 7, "name": "x.png", "mtime": 1.0,
                                           "snippet": "s", "source": "", "ocr_text": ""}]})
        )
        cls.dead = dead_port()
        cls.tools = (
            make_tool("laotu", cls.up_port, bat="捞图-截图记忆库\\启动捞图.bat",
                      search=True, proxy_prefixes=("/api", "/thumb", "/img")),
            make_tool("hukou", cls.dead, search=True, proxy_prefixes=("/api",)),
        )
        cls.launched = []

        class FakeProc:
            pid = 4242

        def fake_launcher(bat_path: Path) -> object:
            cls.launched.append(bat_path)
            return FakeProc()

        cls.httpd = make_server(0, tools=cls.tools, launcher=fake_launcher)
        cls.port = cls.httpd.server_address[1]
        import threading
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.up_srv.shutdown()
        cls.up_srv.server_close()


class TestPagesAndStatus(ServerTestBase):
    def test_index_page(self):
        status, body = http_req(self.port, "/")
        self.assertEqual(status, 200)
        self.assertIn(b"ToolHub", body)
        self.assertIn("工具箱".encode(), body)

    def test_api_tools_mirrors_registry(self):
        status, body = http_req(self.port, "/api/tools")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual([t["id"] for t in data["tools"]],
                         [t.id for t in self.tools])
        self.assertEqual(data["tools"][0]["icon"], "🧰")  # 注入工具未指定 → 默认图标

    def test_tool_view_carries_registry_icon(self):
        from toolhub.server import _tool_view
        from toolhub.registry import get_tool
        self.assertEqual(_tool_view(get_tool("laotu"))["icon"], "🖼️")

    def test_status_parallel_offline_and_online(self):
        status, body = http_req(self.port, "/api/status")
        data = json.loads(body.decode("utf-8"))
        by_id = {s["id"]: s for s in data["status"]}
        self.assertTrue(by_id["laotu"]["online"])
        self.assertFalse(by_id["hukou"]["online"])

    def test_404_json_error(self):
        status, body = http_req(self.port, "/no/such")
        self.assertEqual(status, 404)
        self.assertIn("not found", body.decode("utf-8"))


class TestProxy(ServerTestBase):
    def test_whitelisted_path_proxied(self):
        status, body = http_req(self.port, "/proxy/laotu/api/search?q=abc")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["items"][0]["id"], 7)

    def test_disallowed_path_403(self):
        status, _ = http_req(self.port, "/proxy/laotu/other/x")
        self.assertEqual(status, 403)
        status, _ = http_req(self.port, "/proxy/hukou/secret.txt")
        self.assertEqual(status, 403)

    def test_traversal_rejected(self):
        status, _ = http_req(self.port, "/proxy/laotu/api/../secret")
        self.assertEqual(status, 403)

    def test_unknown_tool_404(self):
        status, _ = http_req(self.port, "/proxy/nosuch/api/x")
        self.assertEqual(status, 404)

    def test_offline_upstream_502(self):
        status, body = http_req(self.port, "/proxy/hukou/api/lookup?q=1")
        self.assertEqual(status, 502)
        self.assertIn("上游不可达", body.decode("utf-8"))


class TestLaunch(ServerTestBase):
    def test_launch_invokes_launcher_with_whitelisted_bat(self):
        status, body = http_req(self.port, "/api/launch", method="POST",
                                body={"id": "laotu"})
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["pid"], 4242)
        expected = WORKSPACE_ROOT / "捞图-截图记忆库" / "启动捞图.bat"
        self.assertEqual(self.launched[-1], expected)
    def test_launch_unknown_id_400(self):
        status, _ = http_req(self.port, "/api/launch", method="POST",
                             body={"id": "../laotu"})
        self.assertEqual(status, 400)

    def test_launch_no_bat_400(self):
        status, body = http_req(self.port, "/api/launch", method="POST",
                                body={"id": "hukou"})
        self.assertEqual(status, 400)  # make_tool 里 hukou 无 bat

    def test_launch_bad_body_400(self):
        status, _ = http_req(self.port, "/api/launch", method="POST", body=None)
        self.assertEqual(status, 400)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/launch", body=b"not json",
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 400)

    def test_launch_oversized_body_400(self):
        # >64KB：应回 400 并断开连接（排空策略），而不是留残包给下一个请求
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("POST", "/api/launch", body=b"x" * (64 * 1024 + 1),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 400)
        finally:
            conn.close()

    def test_launch_missing_file_500(self):
        def broken_launcher(_):
            raise FileNotFoundError("启动脚本不存在: x.bat")
        httpd = make_server(0, tools=self.tools, launcher=broken_launcher)
        import threading
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            status, body = http_req(httpd.server_address[1], "/api/launch",
                                    method="POST", body={"id": "laotu"})
            self.assertEqual(status, 500)
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestFederatedEndpoint(ServerTestBase):
    def test_only_search_tools_fan_out(self):
        # 注册表里 9 个工具只有 3 个参与搜索；结果组必须过滤掉非搜索工具
        status, body = http_req(self.port, "/api/federated?q=x")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual({g["id"] for g in data["groups"]},
                         {"laotu", "hukou"})


class TestRecent(ServerTestBase):
    def test_recent_endpoint_returns_search_tools_only(self):
        status, body = http_req(self.port, "/api/recent")
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual({g["id"] for g in data["groups"]}, {"laotu", "hukou"})
        laotu = next(g for g in data["groups"] if g["id"] == "laotu")
        self.assertTrue(laotu["ok"])
        self.assertEqual(laotu["count"], 1)  # 假上游一条 items
        self.assertEqual(laotu["items"][0]["url"], "/proxy/laotu/thumb/7.jpg")

    def test_recent_per_param_clamped(self):
        status, body = http_req(self.port, "/api/recent?per=999")
        self.assertEqual(status, 200)  # 钳制后正常返回


class TestStop(unittest.TestCase):
    def setUp(self):
        self.killed = []

        def fake_stop(port):
            self.killed.append(port)
            return [4321]

        from toolhub.server import make_server as ms
        self.httpd = ms(0, tools=self.tools, stopper=fake_stop)
        self.port = self.httpd.server_address[1]
        import threading
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()

        def teardown():  # 顺序：先停循环、等线程退出，再关 socket（防 10038）
            self.httpd.shutdown()
            t.join(timeout=5)
            self.httpd.server_close()
        self.addCleanup(teardown)

    @classmethod
    def setUpClass(cls):
        cls._tmp_cls = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp_cls.name)
        cls.store = None  # 占位：ServerTestBase 已含 tools；这里复用其 tools
        cls.tools = (
            make_tool("laotu", 59999, bat="捞图-截图记忆库\\启动捞图.bat"),
            make_tool("hukou", 59998, bat="文件户口本-文件身世\\启动户口本.bat"),
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp_cls.cleanup()

    def test_stop_calls_stopper_with_tool_port(self):
        status, body = http_req(self.port, "/api/stop", method="POST",
                                body={"id": "laotu"})
        d = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(d["ok"])
        self.assertEqual(d["killed"], [4321])
        self.assertEqual(self.killed, [59999])

    def test_stop_no_listener_reports_error(self):
        def nobody(_port):
            return []
        # 单独起一个 stopper 返回空的实例
        from toolhub.server import make_server as ms
        httpd = ms(0, tools=self.tools, stopper=nobody)
        import threading
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            status, body = http_req(httpd.server_address[1], "/api/stop",
                                    method="POST", body={"id": "laotu"})
            d = json.loads(body.decode("utf-8"))
            self.assertEqual(status, 200)
            self.assertFalse(d["ok"])
            self.assertIn("没有在运行", d["error"])
        finally:
            httpd.shutdown()
            t.join(timeout=5)
            httpd.server_close()

    def test_stop_unknown_id_400(self):
        status, _ = http_req(self.port, "/api/stop", method="POST",
                             body={"id": "nope"})
        self.assertEqual(status, 400)

    def test_stop_get_404(self):
        status, _ = http_req(self.port, "/api/stop")
        self.assertEqual(status, 404)


class TestProbeAll(unittest.TestCase):
    def test_probe_all_mixed(self):
        srv, port = make_fake_upstream(lambda p: (200, "ok"))
        try:
            tools = (make_tool("a", port), make_tool("b", dead_port()))
            out = probe_all(tools)
            self.assertTrue(out[0]["online"])
            self.assertFalse(out[1]["online"])
            self.assertEqual([o["id"] for o in out], ["a", "b"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_probe_all_carries_note_for_laotu(self):
        def responder(path):
            if path.startswith("/api/stats"):
                return (200, {"total": 134, "ocr_ok": 0})
            return (200, "ok")

        srv, port = make_fake_upstream(responder)
        try:
            out = probe_all((make_tool("laotu", port, search=True),))
            self.assertTrue(out[0]["online"])
            self.assertIn("待索引 134 张", out[0]["note"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_probe_all_offline_tool_note_none(self):
        out = probe_all((make_tool("laotu", dead_port(), search=True),))
        self.assertIsNone(out[0]["note"])


if __name__ == "__main__":
    unittest.main()
