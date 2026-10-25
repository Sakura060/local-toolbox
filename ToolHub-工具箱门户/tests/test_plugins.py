"""外部插件（万物皆插件 阶段 A）单元测试。

约定：全部插件数据放在临时目录（绝不碰真实 data/plugins）；
probe 用系统自带的 python.exe 做真实识别；不启动任何 GUI 程序。
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

from desktop import cfg, plugins as P, server as desktop_server  # noqa: E402
from desktop.plugins import PluginError  # noqa: E402

PY = sys.executable


def base_manifest(**over):
    m = {
        "manifest_version": 1, "id": "test-tool", "kind": "exe",
        "name": "测试工具", "desc": "",
        "launch": {"target": PY, "workdir": "{target_dir}", "args": [],
                   "use_shell": False},
        "input": {"accept": "none", "via": "argument"},
        "output": {"kind": "window"},
        "lifecycle": {"mode": "oneshot", "close_with_hub": True,
                      "success": "exitcode"},
        "display": {"category": "测试", "tags": [], "aliases": []},
        "trust": {"origin": "self", "confirmed_launch": True},
    }
    m.update(over)
    return m


class ValidateTests(unittest.TestCase):
    def test_valid_examples(self):
        self.assertEqual(P.validate_manifest(base_manifest()), [])
        self.assertEqual(P.validate_manifest(base_manifest(
            kind="url", launch={"target": "https://example.com", "use_shell": True})), [])
        self.assertEqual(P.validate_manifest(base_manifest(
            kind="folder", launch={"target": r"D:\某目录", "use_shell": True})), [])

    def test_rejects_unknown_field(self):
        m = base_manifest()
        m["script"] = "rm -rf"          # 清单无代码红线的探针
        errs = P.validate_manifest(m)
        self.assertTrue(any("不允许的字段" in e for e in errs))

    def test_rejects_bad_kind_and_target(self):
        errs = P.validate_manifest(base_manifest(kind="virus"))
        self.assertTrue(any("kind" in e for e in errs))
        errs = P.validate_manifest(base_manifest(
            launch={"target": "D:\\tools\\readme.txt", "use_shell": False}))
        self.assertTrue(any(".exe" in e for e in errs))

    def test_rejects_bad_url(self):
        errs = P.validate_manifest(base_manifest(
            kind="url", launch={"target": "example.com", "use_shell": True}))
        self.assertTrue(any("协议" in e for e in errs))

    def test_rejects_bad_id(self):
        errs = P.validate_manifest(base_manifest(id="坏ID"))
        self.assertTrue(any("id" in e for e in errs))


class FingerprintTests(unittest.TestCase):
    def test_env_and_case_normalized(self):
        a = base_manifest(launch={"target": "%USERPROFILE%\\Tools\\App.exe"})
        b = base_manifest(launch={"target": Path.home().joinpath("Tools", "APP.EXE").__str__()})
        self.assertEqual(P.fingerprint(a), P.fingerprint(b))

    def test_url_full_string(self):
        a = base_manifest(kind="url", launch={"target": "https://a.com", "use_shell": True})
        b = base_manifest(kind="url", launch={"target": "https://b.com", "use_shell": True})
        self.assertNotEqual(P.fingerprint(a), P.fingerprint(b))


class SlugTests(unittest.TestCase):
    def test_ascii_name(self):
        self.assertEqual(P.slugify("Snipaste Tool"), "snipaste-tool")

    def test_chinese_falls_back(self):
        s = P.slugify("番茄钟")
        self.assertTrue(P._ID_RE.match(s))


class ProbeAndIconTests(unittest.TestCase):
    def test_probe_exe_real(self):
        r = P.probe_path(PY)
        self.assertEqual(r["manifest"]["kind"], "exe")
        self.assertEqual(r["errors"], [])
        self.assertTrue(r["fingerprint"].startswith("exe:"))
        self.assertIsNone(r["duplicate"])

    def test_probe_url(self):
        r = P.probe_path("https://example.com/page")
        self.assertEqual(r["manifest"]["kind"], "url")
        self.assertIn("example.com", r["manifest"]["name"])

    def test_probe_missing_path(self):
        with self.assertRaises(PluginError):
            P.probe_path(r"D:\不存在的路径-xyz\a.exe")

    def test_probe_script_accepted_stage_b(self):
        # 阶段 B：脚本（.py/.bat/.ps1）开放导入，默认一次性任务 + 解释器自动识别
        with tempfile.TemporaryDirectory() as td:
            for name, interp in [("tool.py", "python"), ("run.bat", "cmd"),
                                 ("clean.ps1", "powershell")]:
                s = Path(td) / name
                s.write_text("print(1)", encoding="utf-8")
                r = P.probe_path(str(s))
                self.assertEqual(r["manifest"]["kind"], "script", name)
                self.assertEqual(r["manifest"]["launch"]["interpreter"], interp, name)
                self.assertEqual(r["manifest"]["lifecycle"]["mode"], "oneshot", name)
                self.assertEqual(r["manifest"]["output"]["kind"], "stdout", name)
                self.assertEqual(r["errors"], [], name)

    def test_probe_rejects_sh_script(self):
        with tempfile.TemporaryDirectory() as td:
            s = Path(td) / "deploy.sh"
            s.write_text("echo hi", encoding="utf-8")
            with self.assertRaises(PluginError) as ctx:
                P.probe_path(str(s))
            self.assertIn(".sh", str(ctx.exception))

    def test_icon_extraction_real(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "icon.png"
            ok = P.extract_icon(PY, str(out))
            if ok:  # python.exe 带图标；极端环境无图标则跳过
                from PIL import Image
                with Image.open(out) as img:
                    img.load()
                    self.assertEqual(img.size, (64, 64))

    def test_letter_icon(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "icon.png"
            self.assertTrue(P.letter_icon("测试", "文件整理", str(out)))
            self.assertTrue(out.stat().st_size > 100)

    def test_lnk_resolution(self):
        if Path(PY).name.lower() != "python.exe":
            self.skipTest("需要 python.exe 原名")
        with tempfile.TemporaryDirectory() as td:
            lnk = Path(td) / "测试.lnk"
            lit = str(lnk).replace("'", "''")
            pyt = str(PY).replace("'", "''")
            r = __import__("subprocess").run(
                ["powershell", "-NoProfile", "-Command",
                 f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lit}');"
                 f"$s.TargetPath='{pyt}';$s.Save()"],
                capture_output=True, text=True, timeout=30)
            if not lnk.exists():
                self.skipTest("PowerShell COM 不可用")
            self.assertEqual(r.returncode, 0)
            got = P.resolve_lnk(str(lnk))
            self.assertEqual(Path(got["target"]).resolve(),
                             Path(PY).resolve())
            probed = P.probe_path(str(lnk))
            self.assertEqual(probed["manifest"]["kind"], "exe")
            self.assertEqual(Path(probed["manifest"]["launch"]["target"]).resolve(),
                             Path(PY).resolve())


class StoreAndRunnerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pdir = Path(self._tmp.name) / "plugins"
        self.addCleanup(self._tmp.cleanup)

    def test_save_load_state_roundtrip(self):
        P.save_plugin(base_manifest(), self.pdir,
                      {"draft": False, "enabled": True})
        m = P.load_plugin("test-tool", self.pdir)
        self.assertEqual(m["name"], "测试工具")
        self.assertEqual(m["_state"]["enabled"], True)
        self.assertTrue((P.plugin_dir("test-tool", self.pdir) / "plugin.json").is_file())

    def test_save_rejects_invalid(self):
        with self.assertRaises(PluginError):
            P.save_plugin(base_manifest(script="bad"), self.pdir)

    def test_list_and_remove(self):
        P.save_plugin(base_manifest(), self.pdir, {})
        self.assertEqual(len(P.list_plugins(self.pdir)), 1)
        self.assertTrue(P.remove_plugin("test-tool", self.pdir))
        self.assertEqual(P.list_plugins(self.pdir), [])

    def test_duplicate_detection(self):
        P.save_plugin(base_manifest(), self.pdir, {})
        dup = P.find_duplicate(P.fingerprint(base_manifest()), self.pdir)
        self.assertEqual(dup["id"], "test-tool")

    def test_runner_oneshot_exit_and_log(self):
        runner = P.PluginRunner(self.pdir)
        m = base_manifest(lifecycle={"mode": "oneshot", "close_with_hub": True,
                                     "success": "exitcode"},
                          launch={"target": PY, "workdir": "{target_dir}",
                                  "args": ["-c", "print('plug-ok')"]})
        P.save_plugin(m, self.pdir, {"enabled": True})
        r = runner.launch(m)
        self.assertTrue(r["ok"])
        deadline = time.time() + 15
        while time.time() < deadline and runner.alive("test-tool"):
            time.sleep(0.1)
        self.assertFalse(runner.alive("test-tool"))
        log = (P.plugin_dir("test-tool", self.pdir) / "log.txt").read_text(
            encoding="utf-8", errors="replace")
        self.assertIn("plug-ok", log)
        state = P.get_state("test-tool", self.pdir)
        self.assertEqual(state.get("last_exit"), 0)
        self.assertTrue(state.get("last_ok"))

    def test_runner_missing_target_invalid_message(self):
        runner = P.PluginRunner(self.pdir)
        m = base_manifest(launch={"target": r"D:\不见了\app.exe", "workdir": "",
                                  "args": []})
        P.save_plugin(m, self.pdir, {"enabled": True})
        with self.assertRaises(PluginError) as ctx:
            runner.launch(m)
        self.assertIn("重新定位", str(ctx.exception))

    def test_runner_placeholder_expands_and_runs(self):
        # 阶段 B：参数模板全链路——{file}/{arg:}/{file_stem} 展开后真实跑通
        runner = P.PluginRunner(self.pdir)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in put.txt"          # 带空格中文：验证不二次分词
            src.write_text("hello", encoding="utf-8")
            code = ("import sys,pathlib;pathlib.Path(sys.argv[2])"
                    ".write_text(sys.argv[1]+sys.argv[3],encoding='utf-8')")
            m = base_manifest(
                launch={"target": PY, "workdir": "{target_dir}",
                        "args": ["-c", code, "{file}",
                                 "{file_dir}\\out-{arg:标签}.txt", "{file_stem}"]},
                input={"accept": "file", "via": "argument"})
            P.save_plugin(m, self.pdir, {"enabled": True})
            r = runner.launch(m, inputs={"file": str(src),
                                         "args": {"标签": "ok"}})
            self.assertTrue(r["ok"])
            deadline = time.time() + 15
            while time.time() < deadline and runner.alive("test-tool"):
                time.sleep(0.1)
            out = Path(td) / "out-ok.txt"
            self.assertEqual(out.read_text(encoding="utf-8"),
                             str(src) + "in put")

    def test_state_view_disabled(self):
        runner = P.PluginRunner(self.pdir)
        m = base_manifest()
        P.save_plugin(m, self.pdir, {"enabled": False})
        self.assertEqual(runner.state_view(m)["state"], "disabled")


class PluginApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pdir = Path(self._tmp.name) / "plugins"
        self.addCleanup(self._tmp.cleanup)
        self.httpd = desktop_server.make_server(
            0, tools=None, warnings=[],
            plugin_runner=P.PluginRunner(self.pdir))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def _req(self, method, path, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        conn.request(method, path, body=body,
                     headers={"Content-Type": "application/json"} if body else {})
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        ctype = res.getheader("Content-Type") or ""
        return res.status, (json.loads(raw.decode("utf-8"))
                            if "application/json" in ctype else raw)

    def test_full_import_flow(self):
        # 1 probe
        st, d = self._req("POST", "/api/plugins/probe", {"path": PY})
        self.assertTrue(d["ok"], d.get("error"))
        manifest = d["manifest"]
        # 2 commit
        st, d = self._req("POST", "/api/plugins/commit",
                          {"manifest": manifest, "confirmed_launch": True,
                           "icon_tmp": ""})
        self.assertTrue(d["ok"], d.get("error"))
        pid = d["id"]
        # 3 出现在工具列表（动态加载，无需重启）
        st, d = self._req("GET", "/api/tools")
        ids = [t["id"] for t in d["tools"]]
        self.assertIn(pid, ids)
        view = [t for t in d["tools"] if t["id"] == pid][0]
        self.assertEqual(view["kind"], "plugin")
        self.assertTrue(view["icon_url"].endswith("/icon"))
        # 4 状态在线
        st, d = self._req("GET", "/api/status")
        me = [s for s in d["status"] if s["id"] == pid][0]
        self.assertTrue(me["online"])
        self.assertEqual(me["plugin_state"], "ok")
        # 5 启动（真实进程：python -c 打印后退出）
        manifest["lifecycle"] = {"mode": "oneshot", "close_with_hub": True,
                                 "success": "exitcode"}
        manifest["launch"]["args"] = ["-c", "print('api-plug-ok')"]
        self._req("POST", "/api/plugins/commit",
                  {"manifest": manifest, "confirmed_launch": True, "icon_tmp": ""})
        st, d = self._req("POST", f"/api/plugins/p/{pid}/launch", {})
        self.assertTrue(d["ok"], d.get("error"))
        deadline = time.time() + 15
        while time.time() < deadline:
            st, d2 = self._req("GET", f"/api/plugins/{pid}/log")
            if "api-plug-ok" in (d2.get("log") or ""):
                break
            time.sleep(0.2)
        self.assertIn("api-plug-ok", d2.get("log") or "")
        # 6 禁用后从主界面消失
        self._req("POST", f"/api/plugins/p/{pid}/disable", {})
        st, d = self._req("GET", "/api/tools")
        self.assertNotIn(pid, [t["id"] for t in d["tools"]])
        st, d = self._req("GET", "/api/plugins")
        self.assertIn(pid, [p["id"] for p in d["plugins"]])   # 管理页仍可见
        # 7 启用恢复
        self._req("POST", f"/api/plugins/p/{pid}/enable", {})
        st, d = self._req("GET", "/api/tools")
        self.assertIn(pid, [t["id"] for t in d["tools"]])
        # 8 移除
        self._req("POST", f"/api/plugins/p/{pid}/remove", {})
        st, d = self._req("GET", "/api/plugins")
        self.assertNotIn(pid, [p["id"] for p in d["plugins"]])

    def test_commit_duplicate_flag(self):
        manifest = P.probe_path(PY)["manifest"]
        st, d = self._req("POST", "/api/plugins/commit",
                          {"manifest": manifest, "confirmed_launch": True,
                           "icon_tmp": ""})
        self.assertTrue(d["ok"])
        # 同 id 重提 = 更新（幂等）；不同 id 同目标 = 真重复，要求确认
        other = dict(manifest)
        other["id"] = manifest["id"] + "-alt"
        st, d = self._req("POST", "/api/plugins/commit",
                          {"manifest": other, "confirmed_launch": True,
                           "icon_tmp": ""})
        self.assertFalse(d["ok"])
        self.assertIn("duplicate", d)

    def test_relocate_flow(self):
        manifest = P.probe_path(PY)["manifest"]
        # 指向一个不存在的路径 → 失效 → 重定位回真实路径
        manifest["launch"]["target"] = r"D:\搬家了\ghost.exe"
        st, d = self._req("POST", "/api/plugins/commit",
                          {"manifest": manifest, "confirmed_launch": True,
                           "icon_tmp": ""})
        self.assertTrue(d["ok"])
        pid = d["id"]
        st, d = self._req("GET", "/api/status")
        me = [s for s in d["status"] if s["id"] == pid][0]
        self.assertEqual(me["plugin_state"], "invalid")
        st, d = self._req("POST", f"/api/plugins/p/{pid}/relocate",
                          {"path": PY})
        self.assertTrue(d["ok"], d.get("error"))
        st, d = self._req("GET", "/api/status")
        me = [s for s in d["status"] if s["id"] == pid][0]
        self.assertEqual(me["plugin_state"], "ok")
        # 重定位必须同步搬工作目录（绿色软件相对依赖全靠它）
        st, d = self._req("GET", f"/api/plugins/{pid}")
        self.assertEqual(Path(d["manifest"]["launch"]["workdir"]).resolve(),
                         Path(PY).parent.resolve())

    def test_draft_then_promote(self):
        """回归：草稿完成后必须真正转正（曾因保留旧 draft 标志永不进主界面）。"""
        manifest = P.probe_path(PY)["manifest"]
        st, d = self._req("POST", "/api/plugins/commit",
                          {"manifest": manifest, "draft": True, "icon_tmp": ""})
        self.assertTrue(d["ok"])
        self.assertTrue(d["draft"])
        st, d = self._req("GET", "/api/tools")
        self.assertNotIn(manifest["id"], [t["id"] for t in d["tools"]])
        # 完成导入（draft=False）
        st, d = self._req("POST", "/api/plugins/commit",
                          {"manifest": manifest, "draft": False,
                           "confirmed_launch": True, "icon_tmp": ""})
        self.assertTrue(d["ok"])
        self.assertFalse(d["draft"])
        st, d = self._req("GET", "/api/tools")
        self.assertIn(manifest["id"], [t["id"] for t in d["tools"]])

    def test_commit_rejects_builtin_id(self):
        """回归：插件 id 与内置工具撞名会被注册表静默忽略——必须在导入时拒绝。"""
        manifest = P.probe_path(PY)["manifest"]
        manifest["id"] = "notenest"
        st, d = self._req("POST", "/api/plugins/commit",
                          {"manifest": manifest, "confirmed_launch": True,
                           "icon_tmp": ""})
        self.assertFalse(d["ok"])
        self.assertIn("撞名", d["error"])

    def test_scan_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "App.exe").write_bytes(b"x" * 10)
            (Path(td) / "unins000.exe").write_bytes(b"x")
            st, d = self._req("POST", "/api/plugins/scan", {"path": td})
            self.assertTrue(d["ok"], d.get("error"))
            self.assertEqual(len(d["items"]), 2)
            self.assertTrue(all(i["name"].endswith(".exe") for i in d["items"]))
        st, d = self._req("POST", "/api/plugins/scan",
                          {"path": r"D:\不存在-xyz"})
        self.assertFalse(d["ok"])

    def test_startmenu_endpoint(self):
        st, d = self._req("GET", "/api/plugins/startmenu")
        self.assertTrue(d["ok"], d.get("error"))
        self.assertIsInstance(d["items"], list)

    def test_launch_with_inputs_endpoint(self):
        """阶段 B：/api/launch 对插件透传 inputs（占位符展开走服务器端）。"""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "标记.txt"
            code = ("import sys,pathlib;pathlib.Path(sys.argv[1])"
                    ".write_text(sys.argv[2],encoding='utf-8')")
            m = base_manifest(
                launch={"target": PY, "workdir": "{target_dir}",
                        "args": ["-c", code, "{arg:输出}", "{text}"]},
                input={"accept": "text", "via": "argument"})
            P.save_plugin(m, self.pdir, {"enabled": True})
            st, d = self._req("POST", "/api/launch",
                              {"id": "test-tool",
                               "inputs": {"text": "来自工具箱",
                                          "args": {"输出": str(out)}}})
            self.assertTrue(d["ok"], d.get("error"))
            deadline = time.time() + 10
            while time.time() < deadline and not out.exists():
                time.sleep(0.1)
            self.assertEqual(out.read_text(encoding="utf-8"), "来自工具箱")

    def test_launch_inputs_sanitized(self):
        # inputs 非法类型被清洗而不是 500（file 传 dict → 当作没提供）
        m = base_manifest(launch={"target": PY, "workdir": "{target_dir}",
                                  "args": ["-c", "print('san')"]})
        P.save_plugin(m, self.pdir, {"enabled": True})
        st, d = self._req("POST", "/api/launch",
                          {"id": "test-tool", "inputs": {"file": {"evil": 1}}})
        self.assertTrue(d["ok"], d.get("error"))


class RegistryIntegrationTests(unittest.TestCase):
    def test_load_tools_includes_enabled_plugins(self):
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td) / "plugins"
            P.save_plugin(base_manifest(id="plug-x", name="外挂工具",
                                        display={"category": "外挂", "tags": ["外挂"],
                                                 "aliases": []}),
                          pdir, {"enabled": True})
            orig = P.PLUGINS_DIR
            P.PLUGINS_DIR = pdir
            try:
                tools, warnings = cfg.load_tools()
                ids = [t.id for t in tools]
                self.assertIn("plug-x", ids)
                t = [t for t in tools if t.id == "plug-x"][0]
                self.assertEqual(t.kind, "plugin")
                self.assertEqual(t.source, "plugin")
                self.assertIsNone(t.port)
                self.assertIn("外挂", t.aliases)
            finally:
                P.PLUGINS_DIR = orig


class StageBPlaceholderTests(unittest.TestCase):
    def test_expand_all_kinds(self):
        got = P.expand_placeholders(
            ["-i", "{file}", "--dir", "{file_dir}", "--stem", "{file_stem}",
             "--out", "{folder}", "--note", "{text}", "--p", "{arg:模式}"],
            {"file": r"D:\a b\clip 01.mp4", "folder": r"D:\videos",
             "text": "你好", "args": {"模式": "fast"}})
        self.assertEqual(got, [
            "-i", r"D:\a b\clip 01.mp4", "--dir", r"D:\a b", "--stem", "clip 01",
            "--out", r"D:\videos", "--note", "你好", "--p", "fast"])

    def test_expand_no_split_on_spaces(self):
        # 铁律：替换后的整值是单个 argv 元素，绝不二次分词
        got = P.expand_placeholders(["{file}"], {"file": r"D:\My Tools\a b.exe"})
        self.assertEqual(got, [r"D:\My Tools\a b.exe"])

    def test_missing_inputs_raise(self):
        for args, inputs, needle in [
            (["{file}"], {}, "选择文件"),
            (["{folder}"], {}, "文件夹"),
            (["{text}"], {}, "文本"),
            (["{arg:质量}"], {"args": {}}, "质量"),
            (["{file_stem}"], {}, "选择文件"),
        ]:
            with self.assertRaises(PluginError) as ctx:
                P.expand_placeholders(args, inputs)
            self.assertIn(needle, str(ctx.exception), args)

    def test_plain_args_untouched(self):
        self.assertEqual(P.expand_placeholders(["-y", "-crf", "28"], None),
                         ["-y", "-crf", "28"])

    def test_required_inputs(self):
        m = base_manifest(launch={"target": r"D:\ffmpeg.exe",
                                  "args": ["-i", "{file}", "{arg:crf}",
                                           "{file_dir}\\x.mp4"], "use_shell": False},
                          input={"accept": "file", "via": "argument"},
                          output={"kind": "directory", "dir": "{file_dir}"})
        req = P.required_inputs(m)
        self.assertTrue(req["file"])
        self.assertFalse(req["folder"])
        self.assertFalse(req["text"])
        self.assertEqual(req["args"], ["crf"])

    def test_output_dir_expanded_in_launch_result(self):
        runner = P.PluginRunner(Path(tempfile.mkdtemp()) / "plugins")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "a.mp4"
            src.write_text("x", encoding="utf-8")
            m = base_manifest(
                launch={"target": PY, "workdir": "{target_dir}",
                        "args": ["-c", "print('done')"]},
                output={"kind": "directory", "dir": "{file_dir}"},
                input={"accept": "file", "via": "argument"})
            P.save_plugin(m, runner.plugins_dir, {"enabled": True})
            r = runner.launch(m, inputs={"file": str(src)})
            self.assertTrue(r["ok"])
            self.assertEqual(r.get("output_dir"), str(Path(td)))
            runner.stop("test-tool")


class StageBScanTests(unittest.TestCase):
    def test_scan_blacklist_and_depth(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Snipaste.exe").write_bytes(b"x" * 10)
            (root / "unins000.exe").write_bytes(b"x" * 5)
            (root / "Setup.exe").write_bytes(b"x" * 5)
            (root / "crash_reporter.exe").write_bytes(b"x" * 5)
            sub = root / "sub"
            sub.mkdir()
            (sub / "Inner.exe").write_bytes(b"x" * 3)
            deep = sub / "deep"
            deep.mkdir()
            (deep / "TooDeep.exe").write_bytes(b"x" * 3)
            r = P.scan_dir(str(root))
            names = {i["name"]: i for i in r["items"]}
            self.assertIn("Snipaste.exe", names)
            self.assertIn("Inner.exe", names)          # 一层深以内
            self.assertNotIn("TooDeep.exe", names)     # 超过 depth 不扫
            self.assertTrue(names["unins000.exe"]["blocked"])
            self.assertTrue(names["Setup.exe"]["blocked"])
            self.assertTrue(names["crash_reporter.exe"]["blocked"])
            self.assertFalse(names["Snipaste.exe"]["blocked"])
            # 默认排序：未屏蔽在前，按大小降序
            self.assertEqual(r["items"][0]["name"], "Snipaste.exe")

    def test_scan_missing_dir(self):
        with self.assertRaises(PluginError):
            P.scan_dir(r"D:\不存在目录-xyz-abc")

    def test_startmenu_returns_grouped(self):
        items = P.startmenu_lnks()
        # 真机至少有开始菜单；结构断言（不强求数量，CI/精简系统可少）
        for it in items:
            self.assertTrue(it["path"].lower().endswith(".lnk"))
            self.assertIn("group", it)
        # 无重复
        paths = [i["path"].lower() for i in items]
        self.assertEqual(len(paths), len(set(paths)))


class StageCShareTests(unittest.TestCase):
    """清单分享（阶段 C 一期）：提示词要求的十种情况全覆盖。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pdir = Path(self._tmp.name) / "plugins"
        self.addCleanup(self._tmp.cleanup)

    def _share(self, plugins, version=1, fmt="toolhub-plugin-share"):
        return json.dumps({"format": fmt, "version": version,
                           "exported_at": "2026-10-25T00:00:00",
                           "plugins": plugins}, ensure_ascii=False)

    # 1 正常清单：导出 → 导入 roundtrip（换目录模拟换电脑）
    def test_roundtrip(self):
        m = base_manifest(id="share-tool", name="分享工具",
                          launch={"target": PY, "workdir": "{target_dir}",
                                  "args": [], "use_shell": False})
        P.save_plugin(m, self.pdir, {"enabled": True, "run_count": 42})
        share = P.export_plugins(["share-tool"], self.pdir)
        self.assertEqual(share["format"], "toolhub-plugin-share")
        text = json.dumps(share, ensure_ascii=False)
        # 分享文本不含本机运行状态（state 的 run_count 不在）
        self.assertNotIn("run_count", text)
        other_dir = Path(self._tmp.name) / "other-plugins"
        preview = P.parse_share(text, other_dir)
        item = preview["items"][0]
        self.assertEqual(item["errors"], [])
        self.assertTrue(item["exists"])      # python.exe 这台机器存在
        self.assertIsNone(item["conflict"])
        results = P.import_plugins([item["manifest"]], other_dir)
        self.assertTrue(results[0]["ok"], results)
        loaded = P.load_plugin("share-tool", other_dir)
        self.assertEqual(loaded["name"], "分享工具")
        self.assertEqual((loaded.get("trust") or {}).get("origin"), "shared")
        self.assertEqual(loaded["icon"], "")
        # 图标兜底已生成（字母占位，python.exe 目标存在时是真图标）
        self.assertTrue((P.plugin_dir("share-tool", other_dir) / "icon.png").is_file())

    # 2 未知字段：导出流程来的文件也不豁免白名单
    def test_unknown_field_rejected(self):
        m = base_manifest()
        m["script"] = "rm -rf"
        text = self._share([m])
        preview = P.parse_share(text, self.pdir)
        self.assertTrue(any("不允许的字段" in e for e in preview["items"][0]["errors"]))
        results = P.import_plugins([m], self.pdir)
        self.assertFalse(results[0]["ok"])

    # 3 损坏 JSON → 人话错
    def test_broken_json(self):
        with self.assertRaises(PluginError) as ctx:
            P.parse_share('{"format": "toolhub-plugin-share", "vers', self.pdir)
        self.assertIn("JSON", str(ctx.exception))

    # 4 与现有插件同指纹 → duplicate 冲突标记
    def test_duplicate_conflict_flagged(self):
        P.save_plugin(base_manifest(), self.pdir, {"enabled": True})
        m = base_manifest(id="share-tool", launch={"target": PY,
                                                   "workdir": "", "args": []})
        preview = P.parse_share(self._share([m]), self.pdir)
        self.assertEqual(preview["items"][0]["conflict"]["type"], "duplicate")
        self.assertEqual(preview["items"][0]["conflict"]["id"], "test-tool")

    # 4b 与现有插件同 ID（不同目标）→ id_taken 冲突；rename 换 ID 导入成功
    def test_id_taken_and_rename(self):
        P.save_plugin(base_manifest(), self.pdir, {"enabled": True})
        m = base_manifest(kind="url",
                          launch={"target": "https://example.com", "use_shell": True})
        preview = P.parse_share(self._share([m]), self.pdir)
        self.assertEqual(preview["items"][0]["conflict"]["type"], "id_taken")
        results = P.import_plugins([m], self.pdir, rename={"test-tool": "test-tool-2"})
        self.assertTrue(results[0]["ok"], results)
        self.assertIsNotNone(P.load_plugin("test-tool-2", self.pdir))

    # 5 内置 ID 冲突：server 端拦截（这里测 plugins 层不拦、server 层拦的分工）
    def test_import_plugins_layer_allows_but_server_filters(self):
        m = base_manifest(id="notenest")
        results = P.import_plugins([m], self.pdir)   # plugins 层无内置表，允许
        self.assertTrue(results[0]["ok"])

    # 6 路径不存在：可导入，随后 state_view 判 invalid（待重定位）
    def test_missing_target_imports_as_invalid(self):
        m = base_manifest(id="ghost-tool",
                          launch={"target": r"D:\别人电脑\app.exe", "workdir": "",
                                  "args": []})
        preview = P.parse_share(self._share([m]), self.pdir)
        item = preview["items"][0]
        self.assertFalse(item["exists"])
        self.assertEqual(item["errors"], [])       # 路径不存在不是错误，是状态
        results = P.import_plugins([item["manifest"]], self.pdir)
        self.assertTrue(results[0]["ok"])
        view = P.PluginRunner(self.pdir).state_view(
            P.load_plugin("ghost-tool", self.pdir))
        self.assertEqual(view["state"], "invalid")  # 走既有"重新定位"闭环

    # 7 版本不支持：share.version 未来版 → 拒整文件；manifest_version≠1 → 条目错误
    def test_version_gating(self):
        with self.assertRaises(PluginError) as ctx:
            P.parse_share(self._share([base_manifest()], version=2), self.pdir)
        self.assertIn("版本", str(ctx.exception))
        m = base_manifest()
        m["manifest_version"] = 99
        preview = P.parse_share(self._share([m]), self.pdir)
        self.assertTrue(any("manifest_version" in e for e in preview["items"][0]["errors"]))

    # 8 超长字段 → 校验拒绝
    def test_overlong_fields(self):
        m = base_manifest(desc="长" * 400)
        preview = P.parse_share(self._share([m]), self.pdir)
        self.assertTrue(any("说明过长" in e for e in preview["items"][0]["errors"]))

    # 9 空值/缺字段 → 校验拒绝；条目不是对象 → 条目级错误
    def test_missing_fields_and_non_object(self):
        bad = {"manifest_version": 1}
        preview = P.parse_share(self._share([bad, "not-an-object"]), self.pdir)
        self.assertTrue(preview["items"][0]["errors"])
        self.assertEqual(preview["items"][1]["errors"], ["条目不是 JSON 对象"])
        # plugins 为空 → 文件级错误
        with self.assertRaises(PluginError):
            P.parse_share(self._share([]), self.pdir)

    # 10 部分成功：第二条坏，第一条照常导入，结果逐条报告
    def test_partial_success(self):
        good = base_manifest(id="good-one",
                             launch={"target": PY, "workdir": "", "args": []})
        bad = base_manifest(id="bad-one", kind="virus")
        results = P.import_plugins([good, bad], self.pdir)
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertIsNotNone(P.load_plugin("good-one", self.pdir))


class StageCShareApiTests(PluginApiTests):
    """分享端点：导出/预检/导入 + 内置 ID 在 server 层拦截。"""

    def test_export_import_roundtrip_via_api(self):
        manifest = P.probe_path(PY)["manifest"]
        st, d = self._req("POST", "/api/plugins/commit",
                          {"manifest": manifest, "confirmed_launch": True,
                           "icon_tmp": ""})
        self.assertTrue(d["ok"])
        pid = d["id"]
        st, d = self._req("POST", "/api/plugins/export", {"ids": [pid]})
        self.assertTrue(d["ok"], d.get("error"))
        text = d["text"]
        st, d = self._req("POST", "/api/plugins/import-preview", {"text": text})
        self.assertTrue(d["ok"], d.get("error"))
        # 同机重导入：同 ID 同目标 → id_taken（duplicate 专指不同 ID 同指纹）
        self.assertEqual(d["items"][0]["conflict"]["type"], "id_taken")
        st, d = self._req("POST", "/api/plugins/import",
                          {"items": [manifest], "rename": {manifest["id"]: "imported-x"}})
        self.assertTrue(d["ok"], d.get("error"))
        self.assertTrue(d["results"][0]["ok"])
        self.assertIsNotNone(P.load_plugin("imported-x", self.pdir))

    def test_import_rejects_builtin_id_at_server(self):
        m = base_manifest(id="notenest")
        st, d = self._req("POST", "/api/plugins/import-preview",
                          {"text": self._share_text([m])})
        self.assertTrue(d["ok"])
        self.assertEqual(d["items"][0]["conflict"]["type"], "builtin")
        st, d = self._req("POST", "/api/plugins/import", {"items": [m]})
        self.assertFalse(d["ok"])       # 全部撞名 → 拒绝，绝不覆盖内置

    def _share_text(self, plugins):
        return json.dumps({"format": "toolhub-plugin-share", "version": 1,
                           "plugins": plugins}, ensure_ascii=False)


class StoreAppTests(unittest.TestCase):
    """商店应用（阶段 C 二期，实机可行性已验证后编码）。"""

    def test_probe_store_aumid(self):
        r = P.probe_path("shell:appsfolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                         name_hint="计算器")
        m = r["manifest"]
        self.assertEqual(m["kind"], "store")
        self.assertEqual(m["name"], "计算器")
        self.assertEqual(m["launch"]["aumid"], "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")
        self.assertEqual(m["output"]["kind"], "window")
        self.assertEqual(r["errors"], [])

    def test_probe_store_bad_aumid(self):
        for bad in ("shell:appsfolder\\", "shell:appsfolder\\no-bang",
                    "Shell:Appsfolder\\also-bad"):
            with self.assertRaises(PluginError, msg=bad):
                P.probe_path(bad)

    def test_validate_store_requires_aumid(self):
        errs = P.validate_manifest(base_manifest(
            kind="store", launch={"target": "shell:appsfolder\\x!y",
                                  "aumid": "", "use_shell": True}))
        self.assertTrue(any("aumid" in e for e in errs))

    def test_store_launch_uses_startfile(self):
        opened = []
        runner = P.PluginRunner(Path(tempfile.mkdtemp()) / "plugins")
        m = base_manifest(kind="store",
                          launch={"target": "shell:appsfolder\\pkg_fam!App",
                                  "aumid": "pkg_fam!App", "use_shell": True})
        P.save_plugin(m, runner.plugins_dir, {"enabled": True})
        import unittest.mock as mock
        with mock.patch.object(P.os, "startfile", side_effect=lambda p: opened.append(p)):
            r = runner.launch(m)
        self.assertTrue(r["ok"])
        self.assertEqual(opened, ["shell:appsfolder\\pkg_fam!App"])

    def test_list_store_apps_on_real_machine(self):
        try:
            items = P.list_store_apps()
        except PluginError as exc:
            self.skipTest(f"本机枚举不可用: {exc}")
        # 真机（Win11）应有 UWP；结构断言
        for it in items:
            self.assertIn("!", it["aumid"])
            self.assertTrue(it["name"])
        print(f"  [storeapps] {len(items)} 个")

    def test_storeapps_endpoint(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        pdir = Path(self._tmp.name) / "plugins"
        self.httpd = desktop_server.make_server(
            0, tools=None, warnings=[], plugin_runner=P.PluginRunner(pdir))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

        def shutdown():
            self.httpd.shutdown()
            self.httpd.server_close()
        self.addCleanup(shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request("GET", "/api/plugins/storeapps")
        with conn.getresponse() as r:
            d = json.loads(r.read().decode("utf-8"))
        self.assertTrue(d["ok"], d)
        self.assertIsInstance(d["items"], list)

    def test_store_commit_and_edit_roundtrip(self):
        """store 全链路（不真启动）：probe→commit→注册表出现→edit desc→重定位拒绝非 exe 场景不受影响。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        pdir = Path(self._tmp.name) / "plugins"
        self.httpd = desktop_server.make_server(
            0, tools=None, warnings=[], plugin_runner=P.PluginRunner(pdir))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

        def shutdown():
            self.httpd.shutdown()
            self.httpd.server_close()
        self.addCleanup(shutdown)

        def req(method, path, payload=None):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
            body = json.dumps(payload).encode("utf-8") if payload is not None else None
            conn.request(method, path, body=body,
                         headers={"Content-Type": "application/json"} if body else {})
            with conn.getresponse() as r:
                return json.loads(r.read().decode("utf-8"))

        d = req("POST", "/api/plugins/probe",
                {"path": "shell:appsfolder\\Pkg.Family_abc!App", "name_hint": "测试商店应用"})
        self.assertTrue(d["ok"], d)
        d = req("POST", "/api/plugins/commit",
                {"manifest": d["manifest"], "confirmed_launch": True,
                 "icon_tmp": ""})
        self.assertTrue(d["ok"], d)
        pid = d["id"]
        # 管理列表里出现且状态 ok（store 永远可用）
        d = req("GET", "/api/plugins")
        me = [p for p in d["plugins"] if p["id"] == pid][0]
        self.assertEqual(me["kind"], "store")
        self.assertEqual(me["state"], "ok")
        # 编辑说明（store 无参数模板 → needs_inputs 全空）
        self.assertEqual(me["needs_inputs"],
                         {"file": False, "folder": False, "text": False, "args": []})
        d = req("POST", f"/api/plugins/p/{pid}/edit",
                {"desc": "从商店导入的测试应用"})
        self.assertTrue(d["ok"], d)
        d = req("GET", f"/api/plugins/{pid}")
        self.assertEqual(d["manifest"]["desc"], "从商店导入的测试应用")
        # 分享导出 roundtrip：store 清单也能分享
        d = req("POST", "/api/plugins/export", {"ids": [pid]})
        self.assertTrue(d["ok"], d)
        d = req("POST", "/api/plugins/import-preview",
                {"text": d["text"]})
        self.assertTrue(d["ok"])
        self.assertEqual(d["items"][0]["conflict"]["type"], "id_taken")


if __name__ == "__main__":
    unittest.main()
