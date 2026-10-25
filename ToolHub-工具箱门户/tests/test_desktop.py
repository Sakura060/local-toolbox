"""ToolHub Desktop 单元测试：注册表合并 / 校验 / 启动器 / 服务端点 / 页面资产。

约定与旧测试一致：测试不碰真实工具进程（launcher/stopper 可注入），
settings 用临时文件（save 会写盘，绝不指向真实 desktop.json）。
"""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT.parent))
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from desktop import cfg, envcheck, server as desktop_server  # noqa: E402
from desktop.cfg import BUILTINS, DesktopTool, _validate, load_tools  # noqa: E402
from desktop.launcher import LaunchError, Launcher  # noqa: E402
from desktop import settings as settings_mod  # noqa: E402


def _request(port, method, path, payload=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=body, headers=headers)
    res = conn.getresponse()
    raw = res.read()
    ctype = res.getheader("Content-Type") or ""
    conn.close()
    if "application/json" in ctype:
        return res.status, json.loads(raw.decode("utf-8"))
    return res.status, raw


class FakeLauncher:
    """记录调用，不碰真实进程。"""

    def __init__(self):
        import sys as _sys
        self.python_exe = Path(_sys.executable)
        self.launched = []
        self.forgotten = []
        self.next_result = {"ok": True, "pid": 42, "hint": "ok"}
        self.next_error = None

    def alive(self, tool_id):
        return False

    def started_at(self, tool_id):
        return None

    def forget(self, tool_id):
        self.forgotten.append(tool_id)

    def log_path(self, tool_id):
        return Path(tempfile.gettempdir()) / f"thd-fake-{tool_id}.log"

    def read_log(self, tool_id, lines=200):
        return "fake log line\nsecond line"

    def launch(self, tool, params=None):
        if self.next_error:
            raise LaunchError(self.next_error)
        self.launched.append((tool.id, dict(params or {})))
        return self.next_result


class RegistryTests(unittest.TestCase):
    def test_builtin_count_and_unique_ids(self):
        self.assertEqual(len(BUILTINS), 10)
        ids = [t.id for t in BUILTINS]
        self.assertEqual(len(set(ids)), len(ids))
        for t in BUILTINS:
            self.assertIsNone(_validate(t), t.id)

    def test_load_tools_real_workspace(self):
        """真实工作区集成：10 内置 + apps/番茄钟 被自动识别。"""
        tools, warnings = load_tools()
        by_id = {t.id: t for t in tools}
        self.assertGreaterEqual(len(tools), 11)
        self.assertIn("pomodoro", by_id)
        self.assertEqual(by_id["pomodoro"].category, "专注效率")
        self.assertEqual(by_id["pomodoro"].source, "apps")
        self.assertEqual(by_id["pomodoro"].port, 8661)
        # 内置工具的分类都映射好了
        self.assertIn("fileflow", by_id)
        self.assertEqual(by_id["fileflow"].category, "文件整理")
        # 警告里不应该有内置/示范工具的校验失败
        clean = [w for w in warnings if "pomodoro" in w]
        self.assertEqual(clean, [])

    def test_validate_rejects_bad_entries(self):
        base = dict(id="xa", name="n", desc="d", icon="i", category="c", port=9000,
                    launch_dir="ToolHub-工具箱门户", argv=("{python}", "cli.py"))
        t = DesktopTool(**{**base, "id": "Bad_ID"})
        self.assertIn("id 不合法", _validate(t) or "")
        t = DesktopTool(**{**base, "port": 80})
        self.assertIn("端口", _validate(t) or "")
        t = DesktopTool(**{**base, "argv": ("python", "cli.py")})
        self.assertIn("{python}", _validate(t) or "")
        t = DesktopTool(**{**base, "argv": ("{python}", "{param:p}")})
        self.assertIn("未在 params 声明", _validate(t) or "")
        t = DesktopTool(**{**base, "launch_dir": "../outside"})
        self.assertTrue((_validate(t) or "").startswith("xa:"))

    def test_tool_json_override_and_bad_json(self):
        """tool.json 三种情形：覆盖内置 / 坏 JSON 报警不炸 / apps 重复 id 忽略。"""
        scratch = PROJECT / "data" / "tmp_test_cfg"
        scratch.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: _rmtree(scratch))
        # 坏 JSON
        bad = scratch / "tool.json"
        bad.write_text("{不是json", encoding="utf-8")
        t, warn = cfg._parse_tool_json(bad, "apps")
        self.assertIsNone(t)
        self.assertIn("读取失败", warn)
        # 合法覆盖（用真实存在的目录做 launch_dir）
        good = scratch / "tool.json"
        good.write_text(json.dumps({
            "id": "notenest", "name": "改名后的笔记", "port": 8890,
            "launch": {"argv": ["{python}", "cli.py", "--port", "8890"]},
        }, ensure_ascii=False), encoding="utf-8")
        t, warn = cfg._parse_tool_json(good, "tool.json")
        self.assertIsNone(warn)
        self.assertEqual(t.name, "改名后的笔记")
        self.assertEqual(t.source, "tool.json")


def _rmtree(p: Path):
    import shutil
    shutil.rmtree(p, ignore_errors=True)


class LauncherTests(unittest.TestCase):
    def _tool(self, **kw):
        base = dict(id="t1", name="测试", desc="", icon="🧪", category="测试",
                    port=59001, launch_dir="ToolHub-工具箱门户",
                    argv=("{python}", "-c", "import sys; print('thd-ok'); sys.stdout.flush()"))
        base.update(kw)
        return DesktopTool(**base)

    def test_launch_captures_output_and_forgets_on_exit(self):
        with tempfile.TemporaryDirectory() as td:
            lc = Launcher(python_exe=Path(sys.executable), log_dir=Path(td))
            r = lc.launch(self._tool())
            self.assertTrue(r["ok"])
            self.assertGreater(r["pid"], 0)
            deadline = time.time() + 10
            while time.time() < deadline and lc.alive("t1"):
                time.sleep(0.1)
            self.assertFalse(lc.alive("t1"))
            log = lc.read_log("t1")
            self.assertIn("thd-ok", log)

    def test_launch_rejects_empty_param(self):
        with tempfile.TemporaryDirectory() as td:
            lc = Launcher(python_exe=Path(sys.executable), log_dir=Path(td))
            t = self._tool(argv=("{python}", "cli.py", "{param:dir}"),
                           params=(("dir", ""),))
            with self.assertRaises(LaunchError):
                lc.launch(t)

    def test_launch_rejects_missing_dir(self):
        with tempfile.TemporaryDirectory() as td:
            lc = Launcher(python_exe=Path(sys.executable), log_dir=Path(td))
            t = self._tool(launch_dir="不存在的目录-xyz")
            with self.assertRaises(LaunchError):
                lc.launch(t)

    def test_param_expansion(self):
        with tempfile.TemporaryDirectory() as td:
            lc = Launcher(python_exe=Path(sys.executable), log_dir=Path(td))
            t = self._tool(argv=("{python}", "-c",
                                 "import sys; print('P=' + sys.argv[1])",
                                 "{param:dir}"),
                           params=(("dir", "默认值"),))
            lc.launch(t, {"dir": "自定义目录"})
            deadline = time.time() + 10
            while time.time() < deadline and lc.alive("t1"):
                time.sleep(0.1)
            self.assertIn("P=自定义目录", lc.read_log("t1"))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tools = (
            DesktopTool(id="fa", name="A", desc="", icon="a", category="甲",
                        port=59011, launch_dir="ToolHub-工具箱门户",
                        argv=("{python}", "cli.py"), search=True, proxy=("/api",)),
            DesktopTool(id="fb", name="B", desc="", icon="b", category="乙",
                        port=59012, launch_dir="ToolHub-工具箱门户",
                        argv=("{python}", "cli.py")),
        )
        self.fake = FakeLauncher()
        self.env = [{"id": "c1", "title": "检查", "tool": None, "ok": True,
                     "level": "ok", "detail": "好", "fix": ""}]
        # settings 指到临时文件
        self._tmp = tempfile.TemporaryDirectory()
        settings_mod.SETTINGS_PATH = Path(self._tmp.name) / "desktop.json"
        self.addCleanup(self._tmp.cleanup)
        self.httpd = desktop_server.make_server(
            0, tools=self.tools, warnings=["测试警告"],
            launcher=self.fake, stopper=lambda port: [123],
            envcheck_fn=lambda: self.env)
        self.port = self.httpd.server_address[1]
        import threading
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd.server_close()  # 双调用无害（幂等），防 join 卡住
        self.thread.join(timeout=5)

    def test_tools_endpoint(self):
        status, data = _request(self.port, "GET", "/api/tools")
        self.assertEqual(status, 200)
        self.assertEqual([t["id"] for t in data["tools"]], ["fa", "fb"])
        self.assertEqual(data["categories"], ["甲", "乙"])
        self.assertEqual(data["warnings"], ["测试警告"])
        self.assertIn("version", data["app"])

    def test_status_and_envcheck(self):
        status, data = _request(self.port, "GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual({s["id"] for s in data["status"]}, {"fa", "fb"})
        self.assertFalse(data["status"][0]["online"])  # 测试端口没有真服务
        status, data = _request(self.port, "GET", "/api/envcheck")
        self.assertEqual(data["envcheck"]["checks"], self.env)

    def test_launch_and_stop(self):
        status, data = _request(self.port, "POST", "/api/launch",
                                {"id": "fa", "params": {"x": "1"}})
        self.assertTrue(data["ok"])
        self.assertEqual(self.fake.launched, [("fa", {"x": "1"})])
        status, data = _request(self.port, "POST", "/api/launch", {"id": "nope"})
        self.assertEqual(status, 400)
        self.fake.next_error = "参数「dir」不能为空"
        status, data = _request(self.port, "POST", "/api/launch", {"id": "fa"})
        self.assertFalse(data["ok"])
        self.assertIn("不能为空", data["error"])
        status, data = _request(self.port, "POST", "/api/stop", {"id": "fb"})
        self.assertTrue(data["ok"])
        self.assertEqual(data["killed"], [123])
        self.assertIn("fb", self.fake.forgotten)

    def test_log_endpoint(self):
        status, data = _request(self.port, "GET", "/api/log/fa?lines=10")
        self.assertTrue(data["log"].startswith("fake log"))

    def test_settings_roundtrip_and_id_filter(self):
        status, data = _request(self.port, "GET", "/api/settings")
        self.assertEqual(data["settings"]["favorites"], [])
        status, data = _request(self.port, "POST", "/api/settings",
                                {"favorites": ["fa", "ghost"], "order": ["fb"]})
        self.assertEqual(data["settings"]["favorites"], ["fa"])  # ghost 不在注册表被滤掉
        self.assertEqual(data["settings"]["order"], ["fb"])
        status, data = _request(self.port, "POST", "/api/settings", {"reset": True})
        self.assertEqual(data["settings"]["favorites"], [])

    def test_proxy_whitelist(self):
        status, data = _request(self.port, "GET", "/proxy/fa/static/app.js")
        self.assertEqual(status, 403)  # 不在白名单（fa 只放行 /api）
        status, data = _request(self.port, "GET", "/proxy/fa/api/whatever")
        self.assertEqual(status, 502)  # 白名单内但上游离线 -> 上游不可达
        status, data = _request(self.port, "GET", "/proxy/fb/api/x")
        self.assertEqual(status, 403)  # fb 没有 proxy 前缀

    def test_bad_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/launch", body=b"{broken",)
        res = conn.getresponse()
        self.assertEqual(res.status, 400)
        conn.close()

    def test_page_served(self):
        status, raw = _request(self.port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"ToolHub", raw[:400])


class PageAssetTests(unittest.TestCase):
    """页面资产断言（旧 test_page.py 的桌面版对应物）。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (PROJECT / "desktop" / "page.html").read_text(encoding="utf-8")

    def assert_in_page(self, needle):
        self.assertIn(needle, self.html, f"page.html 缺少关键资产: {needle}")

    def test_layout_assets(self):
        for needle in (
            'id="side"', "renderNav", "cathead", "cardHtml", "data-star",
            "draggable", "enableDrag", "openEmbed", "embedframe",
        ):
            self.assert_in_page(needle)

    def test_required_features(self):
        # 命令面板 / 快捷键 / 主题 / 自检 / 设置导入导出 / 无障碍
        for needle in (
            "Ctrl+K", "palOpen", "cycleTheme", "renderEnv", "renderSettings",
            "expset", "impset", "prefers-reduced-motion", "esc(",
            "first_run_done", "api/federated", "api/envcheck", "api/log/",
        ):
            self.assert_in_page(needle)

    def test_no_raw_html_injection_paths(self):
        # 动态值必须过 esc —— 抽查几个高频插值点
        self.assertIn("esc(t.name)", self.html)
        self.assertIn("esc(t.desc)", self.html)
        self.assertIn('esc(t.url)', self.html)


class EnvcheckTests(unittest.TestCase):
    def test_check_python_shape(self):
        c = envcheck.check_python()
        self.assertEqual(c["level"], "ok")
        self.assertIn("detail", c)

    def test_check_launch_files(self):
        tools, _ = load_tools()
        rows = envcheck.check_launch_files(tools)
        by_tool = {r["tool"]: r for r in rows}
        self.assertTrue(by_tool["notenest"]["ok"])
        self.assertTrue(by_tool["pomodoro"]["ok"])

    def test_summarize(self):
        s = envcheck.summarize([])
        self.assertEqual(s["verdict"], "一切就绪")


if __name__ == "__main__":
    unittest.main()
