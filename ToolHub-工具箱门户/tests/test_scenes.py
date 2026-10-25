"""工作场景（总提示词阶段 1）测试：必测场景全覆盖。

约定：scenes.json 落在临时目录（scenes_mod.SCENES_PATH 可注入 path 参数）；
启动执行器全部注入假件（不真拉起工具），插件集成用真 runner + 真 python oneshot。
"""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT.parent))
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from desktop import cfg, scenes as S, server as desktop_server  # noqa: E402
from desktop.plugins import PluginError  # noqa: E402
import desktop.plugins as P  # noqa: E402


class FakeTool:
    """最小鸭子类型：场景执行只读 id/name/port/kind。"""
    def __init__(self, tid, port=None, kind="builtin"):
        self.id, self.port, self.kind = tid, port, kind
        self.name = "工具-" + tid


class FakeLauncher:
    def __init__(self, fail_ids=()):
        self.calls = []
        self.fail_ids = set(fail_ids)

    def launch(self, tool, params):
        self.calls.append(tool.id)
        if tool.id in self.fail_ids:
            raise RuntimeError("boom-" + tool.id)
        return {"ok": True}


class SceneLogicTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "scenes.json"
        self.addCleanup(self._tmp.cleanup)

    # 1 保存/加载/持久化（重启=重新 load）
    def test_roundtrip_persist(self):
        sc = {"id": "dev", "name": "开发", "items": [
            {"kind": "tool", "ref": "notenest"},
            {"kind": "folder", "ref": str(PROJECT)},
            {"kind": "url", "ref": "https://example.com"}]}
        self.assertEqual(S.validate_scene(sc), [])
        S.save_scenes([S.normalize_scene(sc)], self.path)
        loaded = S.load_scenes(self.path)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["items"][1]["ref"], str(PROJECT))
        self.assertEqual(loaded[0]["icon"], "🎬")

    # 2 空场景：保存允许（用户草稿），启动时报人话错（API 层测）
    def test_empty_scene_saves(self):
        sc = {"id": "empty", "name": "空", "items": []}
        self.assertEqual(S.validate_scene(sc), [])
        S.save_scenes([S.normalize_scene(sc)], self.path)
        self.assertEqual(len(S.load_scenes(self.path)), 1)

    # 3/4 单工具与多工具顺序启动
    def test_launch_order(self):
        launcher = FakeLauncher()
        tools = {"a": FakeTool("a", 1), "b": FakeTool("b", 2)}
        sc = {"id": "x", "name": "X", "items": [
            {"kind": "tool", "ref": "a"}, {"kind": "tool", "ref": "b"}]}
        out = S.launch_scene(sc, tools, launcher=launcher, probe_port=lambda p: False)
        self.assertEqual(launcher.calls, ["a", "b"])
        self.assertTrue(all(r["status"] == "ok" for r in out["results"]))
        self.assertEqual(len(out["session"]["stoppable"]), 2)

    # 5 单项失败继续
    def test_failure_continues(self):
        launcher = FakeLauncher(fail_ids={"bad"})
        tools = {"good": FakeTool("good", 1), "bad": FakeTool("bad", 2)}
        sc = {"id": "x", "name": "X", "items": [
            {"kind": "tool", "ref": "bad"}, {"kind": "tool", "ref": "good"}]}
        out = S.launch_scene(sc, tools, launcher=launcher, probe_port=lambda p: False)
        self.assertEqual(out["results"][0]["status"], "fail")
        self.assertIn("boom-bad", out["results"][0]["detail"])
        self.assertEqual(out["results"][1]["status"], "ok")

    # 6 已运行跳过（判定=端口应答，不猜进程名）；且不进入可停列表
    def test_running_tool_skipped(self):
        launcher = FakeLauncher()
        tools = {"on": FakeTool("on", 1), "off": FakeTool("off", 2)}
        sc = {"id": "x", "name": "X", "items": [
            {"kind": "tool", "ref": "on"}, {"kind": "tool", "ref": "off"}]}
        out = S.launch_scene(sc, tools, launcher=launcher,
                             probe_port=lambda p: p == 1)
        self.assertEqual(launcher.calls, ["off"])          # on 没有被再启动
        self.assertEqual(out["results"][0]["status"], "skip")
        stop_ids = [h.get("id") for h in out["session"]["stoppable"]]
        self.assertEqual(stop_ids, ["off"])                # "on" 不是我们启动的

    # 6b 预览层同样标"已运行"
    def test_preview_marks_running(self):
        tools = {"on": FakeTool("on", 1)}
        sc = {"id": "x", "name": "X", "items": [{"kind": "tool", "ref": "on"}]}
        items = S.preview_scene(sc, tools, probe_port=lambda p: True)
        self.assertEqual(items[0]["status"], "skip_running")

    # 9a 失效引用：预检标记 + 启动该项失败其余继续
    def test_invalid_references(self):
        tools = {}
        sc = {"id": "x", "name": "X", "items": [
            {"kind": "tool", "ref": "ghost"},
            {"kind": "folder", "ref": r"D:\不存在-xyz"},
            {"kind": "url", "ref": "https://example.com"}]}
        items = S.preview_scene(sc, tools)
        self.assertEqual(items[0]["status"], "invalid")
        self.assertEqual(items[1]["status"], "invalid")
        self.assertEqual(items[2]["status"], "ok")
        opened = []
        out = S.launch_scene(sc, tools, probe_port=lambda p: False,
                             start_file=lambda x: opened.append(x))
        self.assertEqual(out["results"][0]["status"], "fail")
        self.assertEqual(out["results"][1]["status"], "fail")
        self.assertEqual(out["results"][2]["status"], "ok")
        self.assertEqual(opened, ["https://example.com"])
        self.assertEqual(out["session"]["untracked"], 1)   # url 不可追踪

    # 9b 文件夹/文件/网址交给系统打开（不可追踪 → 保守不提供停止）
    def test_untracked_not_stoppable(self):
        sc = {"id": "x", "name": "X", "items": [
            {"kind": "folder", "ref": str(PROJECT)}]}
        out = S.launch_scene(sc, {}, start_file=lambda x: None)
        self.assertEqual(out["session"]["stoppable"], [])
        self.assertEqual(out["session"]["untracked"], 1)

    # 10a 损坏 JSON：备份后从空恢复
    def test_corrupt_json_recovers(self):
        good = {"id": "dev", "name": "开发", "items": []}
        S.save_scenes([S.normalize_scene(good)], self.path)
        self.path.write_text("{broken!!!", encoding="utf-8")
        loaded = S.load_scenes(self.path)
        self.assertEqual(loaded, [])
        backups = list(self.path.parent.glob("scenes.json.corrupt-*"))
        self.assertTrue(backups, "坏文件应留档备份")
        self.assertIn("broken", backups[0].read_text(encoding="utf-8"))

    # 10b 重复 ID / 未知字段 / 条目超限 / 非法 URL / 重复条目
    def test_validate_rejections(self):
        errs = S.validate_scene({"id": "dev", "name": "A", "items": []},
                                existing_ids={"dev"})
        self.assertTrue(any("已存在" in e for e in errs))
        bad = {"id": "ok-id", "name": "A", "items": [], "evil": 1}
        self.assertTrue(any("不允许的字段" in e for e in S.validate_scene(bad)))
        many = {"id": "ok-id", "name": "A",
                "items": [{"kind": "url", "ref": "https://a.io"}] * 51}
        self.assertTrue(any("上限" in e for e in S.validate_scene(many)))
        badurl = {"id": "ok-id", "name": "A",
                  "items": [{"kind": "url", "ref": "javascript:alert(1)"}]}
        self.assertTrue(any("http" in e for e in S.validate_scene(badurl)))
        dup = {"id": "ok-id", "name": "A", "items": [
            {"kind": "url", "ref": "https://a.io"},
            {"kind": "url", "ref": "https://a.io"}]}
        self.assertTrue(any("重复" in e for e in S.validate_scene(dup)))

    # 10c 写入失败不破坏原文件（原子性：os.replace 失败 → 原内容完好）
    def test_atomic_write_failure_keeps_original(self):
        good = {"id": "dev", "name": "开发", "items": []}
        S.save_scenes([S.normalize_scene(good)], self.path)
        before = self.path.read_text(encoding="utf-8")
        import os as _os
        real_replace = _os.replace
        def boom(*a, **k):
            raise OSError("disk full")
        _os.replace = boom
        try:
            with self.assertRaises(OSError):
                S.save_scenes([S.normalize_scene(
                    {"id": "other", "name": "B", "items": []})], self.path)
        finally:
            _os.replace = real_replace
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertFalse(list(self.path.parent.glob("*.tmp")), "不应残留 tmp 文件")


class SceneApiTests(unittest.TestCase):
    """HTTP 端点全链路（注入假工具/假启动器/假停止器/假探针）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scenes_path = Path(self._tmp.name) / "scenes.json"
        self._orig_path = S.SCENES_PATH
        S.SCENES_PATH = self.scenes_path            # 端点直接读写模块级路径
        self.launcher = FakeLauncher()
        self.stopped = []
        def fake_stopper(port):
            self.stopped.append(port)
            return True
        self.tools = (FakeTool("t-on", 1), FakeTool("t-off", 2))
        self.httpd = desktop_server.make_server(
            0, tools=self.tools, warnings=[],
            launcher=self.launcher, stopper=fake_stopper,
            plugin_runner=P.PluginRunner(Path(self._tmp.name) / "plugins"),
            probe=lambda port: port == 1)            # t-on "在线"，t-off 离线
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        S.SCENES_PATH = self._orig_path

    def _req(self, method, path, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        conn.request(method, path, body=body,
                     headers={"Content-Type": "application/json"} if body else {})
        with conn.getresponse() as r:
            return json.loads(r.read().decode("utf-8"))

    def _save(self, scene):
        return self._req("POST", "/api/scenes/save", {"scene": scene})

    def test_full_flow_save_preview_launch_stop_remove(self):
        # 保存（空场景允许）
        d = self._save({"id": "mix", "name": "混合", "icon": "🚀", "items": []})
        self.assertTrue(d["ok"], d)
        # 空场景启动 → 人话错
        d = self._req("POST", "/api/scenes/p/mix/launch", {})
        self.assertFalse(d["ok"])
        self.assertIn("空的", d["error"])
        # 编辑成两项
        d = self._save({"id": "mix", "name": "混合", "items": [
            {"kind": "tool", "ref": "t-on"}, {"kind": "tool", "ref": "t-off"}]})
        self.assertTrue(d["ok"])
        # 预览：t-on 已运行（假探针端口 1 在线）→ skip_running
        d = self._req("GET", "/api/scenes/p/preview?id=mix")
        self.assertTrue(d["ok"])
        self.assertEqual(d["items"][0]["status"], "skip_running")
        self.assertEqual(d["items"][1]["status"], "ok")
        # 启动：只真启动 t-off；t-on 跳过
        d = self._req("POST", "/api/scenes/p/mix/launch", {})
        self.assertTrue(d["ok"])
        self.assertEqual(self.launcher.calls, ["t-off"])
        self.assertEqual(d["results"][0]["status"], "skip")
        self.assertEqual(d["results"][1]["status"], "ok")
        session = d["session"]
        # 停止：只停本次启动的（端口 2），绝不停 t-on（端口 1，用户自己开着的）
        d = self._req("POST", "/api/scenes/stop", {"session": session})
        self.assertTrue(d["ok"])
        self.assertEqual(self.stopped, [2])
        # 二次停止同一 session → 明确提示（幂等防呆）
        d = self._req("POST", "/api/scenes/stop", {"session": session})
        self.assertFalse(d["ok"])
        # last_launched 已记录
        scenes = self._req("GET", "/api/scenes")["scenes"]
        self.assertTrue(scenes[0]["last_launched"])
        # 复制 + 删除
        d = self._req("POST", "/api/scenes/p/mix/duplicate", {})
        self.assertTrue(d["ok"])
        self.assertEqual(d["id"], "mix-copy")
        d = self._req("POST", "/api/scenes/p/mix/remove", {})
        self.assertTrue(d["ok"])
        self.assertEqual(len(self._req("GET", "/api/scenes")["scenes"]), 1)

    def test_launch_unknown_tool_fails_item_only(self):
        self._save({"id": "ghosty", "name": "含失效", "items": [
            {"kind": "tool", "ref": "no-such"},
            {"kind": "tool", "ref": "t-off"}]})
        d = self._req("POST", "/api/scenes/p/ghosty/launch", {})
        self.assertTrue(d["ok"])                       # 场景级 ok（部分成功语义）
        self.assertEqual(d["results"][0]["status"], "fail")
        self.assertIn("不存在", d["results"][0]["detail"])
        self.assertEqual(d["results"][1]["status"], "ok")

    def test_save_rejects_bad_scene(self):
        d = self._save({"id": "BAD ID!", "name": "x", "items": []})
        self.assertFalse(d["ok"])
        d = self._save({"id": "ok-id", "name": "x", "items": [], "evil": 1})
        self.assertFalse(d["ok"])
        self.assertIn("不允许的字段", d["error"])

    def test_scene_with_real_plugin(self):
        """场景引用真插件：runner 启动 oneshot python 脚本 → 会话可停。"""
        pdir = Path(self._tmp.name) / "plugins"
        script = Path(self._tmp.name) / "hold.py"
        script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        m = {
            "manifest_version": 1, "id": "hold-plug", "kind": "script",
            "name": "占位脚本", "desc": "",
            "launch": {"target": str(script), "workdir": str(script.parent),
                       "args": [], "interpreter": "python", "use_shell": False},
            "lifecycle": {"mode": "oneshot", "close_with_hub": True,
                          "success": "exitcode"},
        }
        P.save_plugin(m, pdir, {"enabled": True})
        tools = self.tools + (FakeTool("hold-plug", None, kind="plugin"),)
        # 重建服务端以包含新工具表（tools 注入是静态的）
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = desktop_server.make_server(
            0, tools=tools, warnings=[], launcher=self.launcher,
            stopper=lambda p: True,
            plugin_runner=P.PluginRunner(pdir), probe=lambda port: False)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self._save({"id": "plugy", "name": "带插件", "items": [
            {"kind": "tool", "ref": "hold-plug"}]})
        d = self._req("POST", "/api/scenes/p/plugy/launch", {})
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["results"][0]["status"], "ok")
        # 停止该插件（runner 精确句柄）
        d = self._req("POST", "/api/scenes/stop", {"session": d["session"]})
        self.assertTrue(d["ok"])
        self.assertEqual(d["stopped"], ["hold-plug"])


class CrossFeatureTests(unittest.TestCase):
    """交叉路径：场景 × 插件生命周期 × 分享导入（自动化盲区补充）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.scenes_path = Path(self._tmp.name) / "scenes.json"
        self._orig_path = S.SCENES_PATH
        S.SCENES_PATH = self.scenes_path
        self.pdir = Path(self._tmp.name) / "plugins"
        self.addCleanup(self._restore)
        # 真 python oneshot 脚本插件
        script = Path(self._tmp.name) / "noop.py"
        script.write_text("print('noop-ok')\n", encoding="utf-8")
        m = {
            "manifest_version": 1, "id": "noop-plug", "kind": "script",
            "name": "空转脚本", "desc": "",
            "launch": {"target": str(script), "workdir": str(script.parent),
                       "args": [], "interpreter": "python", "use_shell": False},
            "lifecycle": {"mode": "oneshot", "close_with_hub": True,
                          "success": "exitcode"},
        }
        P.save_plugin(m, self.pdir, {"enabled": True})
        self.manifest = m

    def _restore(self):
        S.SCENES_PATH = self._orig_path

    def _tools(self):
        return {"noop-plug": FakeTool("noop-plug", None, kind="plugin")}

    # 场景引用的插件被删除：预览标失效，启动该项失败其余继续
    def test_plugin_removed_after_scene_created(self):
        scene = {"id": "x", "name": "X", "items": [
            {"kind": "tool", "ref": "noop-plug"},
            {"kind": "url", "ref": "https://example.com"}]}
        S.save_scenes([S.normalize_scene(scene)], self.scenes_path)
        P.remove_plugin("noop-plug", self.pdir)          # 插件没了
        items = S.preview_scene(scene, {}, plugin_alive=lambda p: False)
        self.assertEqual(items[0]["status"], "invalid")
        out = S.launch_scene(scene, {}, start_file=lambda x: None,
                             plugin_alive=lambda p: False)
        self.assertEqual(out["results"][0]["status"], "fail")
        self.assertIn("不存在", out["results"][0]["detail"])
        self.assertEqual(out["results"][1]["status"], "ok")

    # 分享导入的插件可以进场景并被启动（真 runner 全链路）
    def test_shared_plugin_in_scene_launches(self):
        share = P.export_plugins(["noop-plug"], self.pdir)
        other_dir = Path(self._tmp.name) / "shared-plugins"
        results = P.import_plugins(
            [share["plugins"][0]], other_dir, rename={"noop-plug": "noop-shared"})
        self.assertTrue(results[0]["ok"], results)
        runner = P.PluginRunner(other_dir)
        scene = {"id": "x", "name": "X", "items": [
            {"kind": "tool", "ref": "noop-shared"}]}
        out = S.launch_scene(scene, {"noop-shared": FakeTool("noop-shared", None,
                                                             kind="plugin")},
                             runner=runner, plugin_alive=runner.alive,
                             plugin_manifests={"noop-shared":
                                               P.load_plugin("noop-shared", other_dir)})
        self.assertEqual(out["results"][0]["status"], "ok", out["results"])
        self.assertEqual(out["session"]["stoppable"],
                         [{"type": "plugin", "id": "noop-shared"}])
        # 等 oneshot 跑完且日志写完（_watch 持有 log.txt 句柄，不写完会卡住 tmp 清理）
        log_file = P.plugin_dir("noop-shared", other_dir) / "log.txt"
        deadline = time.time() + 15
        while time.time() < deadline:
            if not runner.alive("noop-shared"):
                try:
                    if "进程退出" in log_file.read_text(encoding="utf-8",
                                                        errors="replace"):
                        break
                except OSError:
                    pass
            time.sleep(0.2)

    # 场景条目的 folder 支持环境变量展开（%TEMP% 在两台机器语义一致）
    def test_folder_env_expansion(self):
        import os
        real_tmp = os.environ.get("TEMP", "")
        scene = {"id": "x", "name": "X", "items": [
            {"kind": "folder", "ref": "%TEMP%"}]}
        items = S.preview_scene(scene, {})
        self.assertEqual(items[0]["status"], "ok", items[0])
        self.assertEqual(items[0].get("ref_expanded"), real_tmp)
        opened = []
        S.launch_scene(scene, {}, start_file=lambda p: opened.append(p))
        self.assertEqual(opened, [real_tmp])


if __name__ == "__main__":
    unittest.main()
