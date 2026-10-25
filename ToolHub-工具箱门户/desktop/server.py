"""ToolHub Desktop HTTP 服务：新 UI + 插件注册表 + 无窗口启动/停止 + 环境自检。

端点一览（全部 127.0.0.1，默认端口 8650）：
- GET  /                 桌面版页面（desktop/page.html）
- GET  /api/tools        合并注册表 + 分类 + 警告 + 应用信息
- GET  /api/status       并行探测全部工具 -> [{id, online, ms, note}]
- GET  /api/federated    联邦搜索（复用 toolhub.federated，鸭子类型传 DesktopTool）
- GET  /api/recent       最近动态
- GET  /api/log/<id>     工具运行日志尾部（桌面版拉起实例的输出）
- GET  /api/envcheck     环境自检（人话版清单）
- GET  /api/settings     收藏/排序/首启标记
- POST /api/settings     保存设置（校验 id 白名单）
- POST /api/launch       {id, params} 无窗口拉起 + 日志捕获
- POST /api/stop         {id} 端口反查 PID 树杀（复用 toolhub.procs）
- GET  /proxy/<id>/...   段边界白名单只读透传（复用 toolhub.proxy）

测试可注入：tools / launcher / stopper / envcheck_fn 全部可替换。
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from toolhub import federated, proxy
from toolhub import procs

import uuid

from . import __version__, WORKSPACE_ROOT
from . import cfg as cfg_mod
from . import envcheck as envcheck_mod
from . import settings as settings_mod
from . import plugins as plugins_mod
from . import scenes as scenes_mod
from .cfg import DesktopTool, tool_view
from .launcher import Launcher, LaunchError
from .plugins import PluginError, PluginRunner

PAGE_PATH = Path(__file__).resolve().parent / "page.html"


def stop_tool(port: int) -> list[int]:
    return procs.kill_pids(procs.find_port_pids(port))


def probe_all(tools: tuple[DesktopTool, ...]) -> list[dict]:
    results: dict[str, dict] = {}
    lock = threading.Lock()
    live_tools = tuple(t for t in tools if t.port is not None)

    def work(t: DesktopTool) -> None:
        online, ms = proxy.probe(t.port, "/")
        entry = {"id": t.id, "online": online, "ms": ms, "note": None}
        if online:
            note = federated.tool_note(t)
            if note:
                entry["note"] = note
        with lock:
            results[t.id] = entry

    threads = [threading.Thread(target=work, args=(t,), daemon=True) for t in live_tools]
    for th in threads:
        th.start()
    for th in threads:
        th.join(proxy.PROBE_TIMEOUT + federated.FED_TIMEOUT + 0.5)
    return [results.get(t.id, {"id": t.id, "online": False, "ms": 0, "note": None})
            for t in live_tools]


def plugin_status_all(runner: PluginRunner,
                      tools: tuple[DesktopTool, ...]) -> list[dict]:
    """外部插件状态（无端口可探，只查文件/进程/停用标记）。"""
    out = []
    for t in tools:
        if t.kind != "plugin":
            continue
        m = plugins_mod.load_plugin(t.id, runner.plugins_dir)
        if m is None:
            continue
        view = runner.state_view(m)
        label = {"ok": "可用", "invalid": "已失效", "disabled": "已停用",
                 "draft": "草稿"}.get(view["state"], view["state"])
        out.append({"id": t.id, "online": view["state"] == "ok",
                    "ms": 0, "note": None,
                    "plugin_state": view["state"],
                    "plugin_label": label,
                    "running": view["running"]})
    return out


def _sanitize_inputs(raw) -> dict | None:
    """启动表单输入的白名单清洗：{file,folder,text: str, args: {str: str}}。
    非法类型一律丢弃对应键；整体不是 dict 则返回 None（= 没有输入）。"""
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    for key in ("file", "folder", "text"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()
    args = raw.get("args")
    if isinstance(args, dict):
        clean_args = {str(k)[:60]: str(v)[:1000]
                      for k, v in args.items()
                      if isinstance(k, str) and isinstance(v, str)}
        if clean_args:
            out["args"] = clean_args
    return out or None


def make_server(port: int,
                tools: tuple[DesktopTool, ...] | None = None,
                warnings: list[str] | None = None,
                launcher: Launcher | None = None,
                stopper=stop_tool,
                envcheck_fn=None,
                plugin_runner: PluginRunner | None = None,
                probe=None) -> ThreadingHTTPServer:
    if tools is None:
        warnings = []          # 工具列表每次请求动态加载（插件增删即生效）
    launcher = launcher if launcher is not None else Launcher()
    runner = plugin_runner if plugin_runner is not None else PluginRunner()
    probe_fn = probe or proxy.probe   # 场景"已运行"判定（测试可注入假探针）
    # 注册表层与运行器必须指向同一个插件目录（测试注入临时目录时尤其重要）
    plugins_mod.PLUGINS_DIR = Path(runner.plugins_dir)
    envcheck_fn = envcheck_fn or (lambda: envcheck_mod.run_all(tools or cfg_mod.load_tools()[0]))
    # 场景启动会话（内存态：服务重启即失效——停止能力只对本次会话有效，见 scenes.py 设计）
    scene_sessions: dict[str, dict] = {}

    def current_tools() -> tuple[DesktopTool, ...]:
        if tools is not None:
            return tools
        return cfg_mod.load_tools()[0]

    def scene_env() -> tuple[dict, dict]:
        """(tools_by_id, plugin_manifests)——场景预检/启动的引用解析。"""
        all_tools = current_tools()
        by_id = {t.id: t for t in all_tools}
        manifests = {}
        for t in all_tools:
            if t.kind == "plugin":
                m = plugins_mod.load_plugin(t.id, runner.plugins_dir)
                if m is not None:
                    manifests[t.id] = m
        return by_id, manifests

    class Handler(BaseHTTPRequestHandler):
        server_version = f"ToolHubDesktop/{__version__}"

        def log_message(self, fmt, *args):
            pass

        # ---------- 基础 ----------
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, status: int, msg: str) -> None:
            self._json({"error": msg}, status)

        def _query(self) -> dict[str, str]:
            qs = parse_qs(urlparse(self.path).query)
            return {k: v[0] for k, v in qs.items() if v}

        def _body(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 64 * 1024:
                if 0 < length <= 1024 * 1024:
                    self.rfile.read(length)
                self.close_connection = True
                return None
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
            return data if isinstance(data, dict) else None

        # ---------- GET ----------
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                body = PAGE_PATH.read_bytes()
                self._send(200, body, "text/html; charset=utf-8")
            elif path == "/api/tools":
                all_tools = current_tools()
                cats: list[str] = []
                for t in all_tools:
                    if t.category not in cats:
                        cats.append(t.category)
                self._json({
                    "tools": [tool_view(t) for t in all_tools],
                    "categories": cats,
                    "warnings": warnings,
                    "app": {
                        "version": __version__,
                        "python": f"{__import__('sys').version_info.major}."
                                  f"{__import__('sys').version_info.minor}."
                                  f"{__import__('sys').version_info.micro}",
                        "workspace": str(WORKSPACE_ROOT),
                        "bundled_python": launcher.python_exe.name == "python.exe"
                                          and "runtime" in str(launcher.python_exe),
                        "python_exe": str(launcher.python_exe),
                        "bridge": tools is None,   # 壳窗口模式才有文件选择桥
                    },
                })
            elif path == "/api/status":
                all_tools = current_tools()
                status = probe_all(all_tools) + plugin_status_all(runner, all_tools)
                self._json({"status": status})
            elif path == "/api/federated":
                q = self._query().get("q", "")
                try:
                    limit = max(1, min(50, int(self._query().get("limit", "12"))))
                except ValueError:
                    limit = 12
                subset = tuple(t for t in current_tools() if t.search)
                self._json(federated.federated_search(q, tools=subset, limit=limit))
            elif path == "/api/recent":
                try:
                    per = max(1, min(20, int(self._query().get("per", "5"))))
                except ValueError:
                    per = 5
                subset = tuple(t for t in current_tools() if t.search)
                self._json(federated.recent(subset, per_tool=per))
            elif path == "/api/plugins":
                items = []
                for m in plugins_mod.list_plugins(runner.plugins_dir):
                    view = runner.state_view(m)
                    items.append({
                        "id": m["id"], "kind": m.get("kind"), "name": m.get("name"),
                        "desc": m.get("desc", ""),
                        "category": (m.get("display") or {}).get("category", ""),
                        "state": view["state"],
                        "running": view["running"],
                        "source": (m.get("trust") or {}).get("origin", "self"),
                        "imported_at": m.get("imported_at", ""),
                        "last_launched": (m.get("_state") or {}).get("last_launched"),
                        "run_count": (m.get("_state") or {}).get("run_count", 0),
                        "icon_url": f"/api/plugins/{m['id']}/icon",
                        "target": (m.get("launch") or {}).get("target", ""),
                        "needs_inputs": plugins_mod.required_inputs(m),
                    })
                self._json({"plugins": items})
            elif path == "/api/scenes":
                scenes = scenes_mod.load_scenes()
                self._json({"scenes": scenes})
            elif path == "/api/scenes/p/preview":
                # ?id=xxx 场景启动预览（逐项状态，不执行）
                sid = self._query().get("id", "")
                scene = next((s for s in scenes_mod.load_scenes()
                              if s.get("id") == sid), None)
                if scene is None:
                    self._error(404, "没有这个场景")
                    return
                by_id, _ = scene_env()
                items = scenes_mod.preview_scene(
                    scene, by_id, probe_port=probe_fn,
                    plugin_alive=runner.alive)
                self._json({"ok": True, "scene": scene, "items": items})
            elif path == "/api/plugins/startmenu":
                # 阶段 B：开始菜单快捷方式列表（只列名不解析，选中再走 probe）
                try:
                    items = plugins_mod.startmenu_lnks()
                except Exception as exc:  # noqa: BLE001
                    self._json({"ok": False, "error": f"读开始菜单失败（{type(exc).__name__}）"})
                    return
                self._json({"ok": True, "items": items})
            elif path == "/api/plugins/storeapps":
                # 阶段 C 二期：商店应用（UWP/MSIX）枚举（Get-StartApps）
                try:
                    items = plugins_mod.list_store_apps()
                except PluginError as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                except Exception as exc:  # noqa: BLE001
                    self._json({"ok": False, "error": f"枚举失败（{type(exc).__name__}）"})
                    return
                self._json({"ok": True, "items": items})
            elif path.startswith("/api/plugins/") and path.endswith("/icon"):
                pid = path[len("/api/plugins/"):-len("/icon")]
                m = plugins_mod.load_plugin(pid, runner.plugins_dir)
                if m is None:
                    self._error(404, "未知插件")
                    return
                icon = plugins_mod.plugin_dir(pid, runner.plugins_dir) / str(
                    m.get("icon") or "icon.png")
                if not icon.is_file():
                    self._error(404, "无图标")
                    return
                self._send(200, icon.read_bytes(), "image/png")
            elif path == "/api/envcheck":
                self._json({"envcheck": envcheck_mod.summarize(envcheck_fn())})
            elif path == "/api/settings":
                self._json({"settings": settings_mod.load()})
            elif path.startswith("/api/log/"):
                tid = path[len("/api/log/"):]
                tool = {t.id: t for t in current_tools()}.get(tid)
                if tool is None or tool.kind == "plugin":
                    self._error(404, "未知工具")
                    return
                try:
                    lines = max(10, min(1000, int(self._query().get("lines", "200"))))
                except ValueError:
                    lines = 200
                alive = launcher.alive(tid)
                self._json({"id": tid, "alive": alive,
                            "started_at": launcher.started_at(tid),
                            "log": launcher.read_log(tid, lines)})
            elif path.startswith("/api/plugins/") and path.endswith("/log"):
                pid = path[len("/api/plugins/"):-len("/log")]
                if plugins_mod.load_plugin(pid, runner.plugins_dir) is None:
                    self._error(404, "未知插件")
                    return
                log_file = plugins_mod.plugin_dir(pid, runner.plugins_dir) / "log.txt"
                text = ""
                if log_file.is_file():
                    try:
                        text = log_file.read_text(encoding="utf-8", errors="replace")[-8192:]
                    except OSError:
                        pass
                self._json({"id": pid, "alive": runner.alive(pid), "log": text})
            elif path.startswith("/api/plugins/"):
                pid = path[len("/api/plugins/"):]
                m = plugins_mod.load_plugin(pid, runner.plugins_dir)
                if m is None:
                    self._error(404, "未知插件")
                    return
                self._json({"manifest": {k: v for k, v in m.items() if not k.startswith("_")},
                            "state": m.get("_state") or {}})
            elif path.startswith("/proxy/"):
                self._handle_proxy(path)
            else:
                self._error(404, "not found")

        # ---------- POST ----------
        def do_POST(self) -> None:
            path = urlparse(self.path).path
            known = ("/api/launch", "/api/stop", "/api/settings",
                     "/api/plugins/probe", "/api/plugins/commit",
                     "/api/plugins/test", "/api/plugins/scan", "/api/open-dir",
                     "/api/plugins/export", "/api/plugins/import-preview",
                     "/api/plugins/import",
                     "/api/scenes/save", "/api/scenes/stop")
            if path not in known and not path.startswith("/api/plugins/p/") \
                    and not path.startswith("/api/scenes/p/"):
                self._error(404, "not found")
                return
            data = self._body()
            if data is None:
                self._error(400, "请求体不合法")
                return
            if path == "/api/settings":
                self._handle_settings(data)
            elif path == "/api/launch":
                self._handle_launch(data)
            elif path == "/api/stop":
                self._handle_stop(data)
            elif path == "/api/plugins/probe":
                self._plugin_probe(data)
            elif path == "/api/plugins/commit":
                self._plugin_commit(data)
            elif path == "/api/plugins/test":
                self._plugin_test(data)
            elif path == "/api/plugins/scan":
                self._plugin_scan(data)
            elif path == "/api/open-dir":
                # 打开输出文件夹（oneshot 命令行工具的"结果在哪"入口）。
                # 只接受真实存在的目录（防路径注入/误开文件）。
                p = Path(plugins_mod.expand_path(str(data.get("dir") or "")))
                if not p.is_dir():
                    self._json({"ok": False, "error": f"目录不存在：{p}"})
                    return
                try:
                    os.startfile(str(p))  # noqa: S606
                except OSError as exc:
                    self._json({"ok": False, "error": f"打开失败：{exc}"})
                    return
                self._json({"ok": True})
            elif path == "/api/plugins/export":
                # 导出可分享清单（单个或批量）。只含白名单字段，绝不带运行状态。
                ids = data.get("ids")
                if not isinstance(ids, list) or not ids \
                        or not all(isinstance(x, str) for x in ids) or len(ids) > 100:
                    self._json({"ok": False, "error": "ids 需要是非空字符串数组（上限 100）"})
                    return
                try:
                    share = plugins_mod.export_plugins(ids, runner.plugins_dir)
                except PluginError as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                body = json.dumps(share, ensure_ascii=False, indent=2)
                self._json({"ok": True, "text": body})
            elif path == "/api/plugins/import-preview":
                # 导入前预检：逐条校验/冲突/路径存在性，不落盘。
                text = str(data.get("text") or "")
                if not (1 <= len(text) <= 512 * 1024):
                    self._json({"ok": False, "error": "分享文件内容为空或超过 512KB 上限"})
                    return
                try:
                    result = plugins_mod.parse_share(text, runner.plugins_dir)
                except PluginError as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                builtin_ids = {t.id for t in cfg_mod.BUILTINS} | \
                              {t.id for t in cfg_mod._apps_scan([])}
                for item in result["items"]:
                    m = item.get("manifest")
                    if m and not item["errors"] and str(m.get("id")) in builtin_ids:
                        item["conflict"] = {"type": "builtin",
                                            "id": m["id"], "name": m.get("name", "")}
                self._json({"ok": True, **result})
            elif path == "/api/plugins/import":
                # 确认导入：逐条完整再校验（不因经过预检而豁免），部分成功语义。
                manifests = data.get("items")
                rename = data.get("rename")
                if not isinstance(manifests, list) or not manifests \
                        or not all(isinstance(x, dict) for x in manifests) \
                        or len(manifests) > 100:
                    self._json({"ok": False, "error": "items 需要是非空对象数组（上限 100）"})
                    return
                if rename is not None and not (
                        isinstance(rename, dict)
                        and all(isinstance(k, str) and isinstance(v, str)
                                for k, v in rename.items())):
                    self._json({"ok": False, "error": "rename 需要是 {旧id: 新id} 对象"})
                    return
                builtin_ids = {t.id for t in cfg_mod.BUILTINS} | \
                              {t.id for t in cfg_mod._apps_scan([])}
                clean = []
                for raw in manifests:
                    rid = str(raw.get("id", ""))
                    if str((rename or {}).get(rid, rid)) in builtin_ids:
                        # 内置撞名在导入端点也拦一道（预检之后清单可能被改动）
                        continue
                    clean.append(raw)
                if not clean:
                    self._json({"ok": False,
                                "error": "没有可导入的条目（全部与内置工具撞名）"})
                    return
                results = plugins_mod.import_plugins(clean, runner.plugins_dir,
                                                     rename or {})
                self._json({"ok": True, "results": results})
            elif path == "/api/scenes/save":
                self._scene_save(data)
            elif path == "/api/scenes/stop":
                self._scene_stop(data)
            elif path.startswith("/api/scenes/p/"):
                self._scene_action(path[len("/api/scenes/p/"):], data)
            else:
                self._plugin_manage(path[len("/api/plugins/p/"):], data)

        # ---------- 插件 ----------
        def _plugin_probe(self, data: dict) -> None:
            path = str(data.get("path") or "")
            try:
                result = plugins_mod.probe_path(path, runner.plugins_dir,
                                                name_hint=str(data.get("name_hint") or ""))
            except PluginError as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False,
                            "error": f"识别失败（{type(exc).__name__}）"})
                return
            self._json({"ok": True, **result})

        def _plugin_commit(self, data: dict) -> None:
            m = data.get("manifest")
            if not isinstance(m, dict):
                self._error(400, "缺少 manifest")
                return
            m.setdefault("trust", {})
            m["trust"]["confirmed_launch"] = bool(data.get("confirmed_launch"))
            m.setdefault("imported_at", __import__("time").strftime("%Y-%m-%dT%H:%M:%S"))
            draft = bool(data.get("draft"))
            errors = plugins_mod.validate_manifest(m)
            if errors:
                self._json({"ok": False, "error": "；".join(errors)})
                return
            # id 不得与内置工具/apps 撞名（否则注册表会静默忽略这个插件）
            builtin_ids = {t.id for t in cfg_mod.BUILTINS}
            builtin_ids |= {t.id for t in cfg_mod._apps_scan([])}
            if not draft and m["id"] in builtin_ids:
                self._json({"ok": False,
                            "error": f"名称「{m['id']}」和工具箱内置工具撞名了，请改一个显示名再导入"})
                return
            fp = plugins_mod.fingerprint(m)
            dup = plugins_mod.find_duplicate(fp, runner.plugins_dir)
            if dup and not data.get("allow_duplicate") and dup["id"] != m.get("id"):
                self._json({"ok": False,
                            "duplicate": {"id": dup["id"], "name": dup.get("name")},
                            "error": f"这个软件已经在工具箱里了（{dup.get('name')}）"})
                return
            # 保留既有统计；draft/enabled 以本次请求为准（完成草稿必须真正转正）
            state = {"draft": draft, "enabled": True}
            old = plugins_mod.load_plugin(m["id"], runner.plugins_dir)
            if old:
                old_state = old.get("_state") or {}
                state["run_count"] = old_state.get("run_count", 0)
                state["last_launched"] = old_state.get("last_launched")
                if "enabled" in old_state and old_state.get("enabled") is False and draft:
                    state["enabled"] = False   # 编辑草稿不改变启用状态
                elif old_state.get("enabled") is False and not draft:
                    # 编辑一个已停用的插件：保持停用（用户可在管理页手动启用）
                    state["enabled"] = False
            try:
                plugins_mod.save_plugin(m, runner.plugins_dir, state)
            except PluginError as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            icon_tmp = str(data.get("icon_tmp") or "")
            pdir = plugins_mod.plugin_dir(m["id"], runner.plugins_dir)
            icon_name = str(m.get("icon") or "icon.png")
            icon_path = pdir / icon_name
            if icon_tmp and Path(icon_tmp).is_file():
                icon_path.write_bytes(Path(icon_tmp).read_bytes())
                Path(icon_tmp).unlink(missing_ok=True)
            elif not icon_path.is_file():
                plugins_mod.letter_icon(m.get("name", ""), (m.get("display") or {})
                                        .get("category", ""), str(icon_path))
            self._json({"ok": True, "id": m["id"], "draft": state["draft"]})

        def _plugin_test(self, data: dict) -> None:
            m = data.get("manifest")
            if not isinstance(m, dict):
                self._error(400, "缺少 manifest")
                return
            errors = plugins_mod.validate_manifest(m)
            if errors:
                self._json({"ok": False, "error": "；".join(errors)})
                return
            # 试运行不落盘：直接按清单执行一次（用户已在向导里看过"将执行什么"）
            ghost = dict(m)
            ghost["id"] = m["id"]
            try:
                result = runner.launch(ghost, _sanitize_inputs(data.get("inputs")))
                self._json({"ok": True, **result})
            except PluginError as exc:
                self._json({"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": f"试运行失败（{type(exc).__name__}）"})

        def _plugin_scan(self, data: dict) -> None:
            # 阶段 B：批量扫描一个目录找 exe/.lnk 候选（黑名单由 plugins 层过滤）
            try:
                depth = max(0, min(2, int(data.get("depth", 1))))
            except (TypeError, ValueError):
                depth = 1
            try:
                result = plugins_mod.scan_dir(str(data.get("path") or ""), depth=depth)
            except PluginError as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": f"扫描失败（{type(exc).__name__}）"})
                return
            self._json({"ok": True, **result})

        def _plugin_manage(self, action: str, data: dict) -> None:
            pid, _, sub = action.partition("/")
            m = plugins_mod.load_plugin(pid, runner.plugins_dir)
            if m is None:
                self._error(404, "未知插件")
                return
            if sub == "launch":
                try:
                    result = runner.launch(m, _sanitize_inputs(data.get("inputs")))
                    self._json(result)
                except PluginError as exc:
                    self._json({"ok": False, "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    self._json({"ok": False, "error": f"启动失败（{type(exc).__name__}）"})
            elif sub == "stop":
                killed = runner.stop(pid)
                self._json({"ok": bool(killed), "killed": killed,
                            "error": None if killed else "它没有在运行"})
            elif sub == "relocate":
                new_path = str(data.get("path") or "")
                p = Path(plugins_mod.expand_path(new_path))
                if not p.exists():
                    self._json({"ok": False, "error": f"新位置不存在：{p}"})
                    return
                launch = m.setdefault("launch", {})
                if m.get("kind") == "exe" and p.suffix.lower() != ".exe":
                    self._json({"ok": False, "error": "要选一个 .exe 文件"})
                    return
                launch["target"] = str(p)
                # 工作目录必须跟着搬（绿色软件的相对依赖全靠它）——
                # 保留旧目录会让搬家后的程序一启动就找不到自己的文件
                if m.get("kind") == "exe":
                    launch["workdir"] = str(p.parent)
                m.setdefault("trust", {})["resolved_from"] = launch.get("target")
                state = {"draft": False}
                try:
                    plugins_mod.save_plugin(m, runner.plugins_dir, {**plugins_mod.get_state(pid, runner.plugins_dir), **state})
                except PluginError as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                self._json({"ok": True, "hint": "已指向新位置，恢复可用"})
            elif sub == "edit":
                for key in ("name", "desc"):
                    if key in data and isinstance(data[key], str):
                        m[key] = data[key][:60 if key == "name" else 300]
                if "category" in data and isinstance(data["category"], str):
                    m.setdefault("display", {})["category"] = data["category"][:30]
                if "tags" in data and isinstance(data["tags"], list):
                    m["display"]["tags"] = [str(x)[:20] for x in data["tags"][:10]]
                if "args" in data and isinstance(data["args"], list):
                    m["launch"]["args"] = [str(x)[:500] for x in data["args"][:20]]
                if "elevate" in data:
                    m["launch"]["elevate"] = bool(data["elevate"])
                if "window" in data and data["window"] in ("normal", "minimized", "maximized", "hidden"):
                    m["launch"]["window"] = data["window"]
                try:
                    plugins_mod.save_plugin(m, runner.plugins_dir)
                except PluginError as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                self._json({"ok": True})
            elif sub == "disable":
                plugins_mod.set_state(pid, {"enabled": False}, runner.plugins_dir)
                self._json({"ok": True})
            elif sub == "enable":
                plugins_mod.set_state(pid, {"enabled": True, "draft": False},
                                      runner.plugins_dir)
                self._json({"ok": True})
            elif sub == "remove":
                ok = plugins_mod.remove_plugin(pid, runner.plugins_dir)
                self._json({"ok": ok})
            else:
                self._error(404, "未知插件操作")

        # ---------- 工作场景（阶段 1）----------
        def _scene_save(self, data: dict) -> None:
            """新建或更新一个场景（id 已存在=更新）。"""
            scene = data.get("scene")
            if not isinstance(scene, dict):
                self._json({"ok": False, "error": "缺少 scene"})
                return
            scenes = scenes_mod.load_scenes()
            # 更新场景时排除自己——只有"撞别人"才算重复（新建=撞所有人）
            existing = {s.get("id") for s in scenes
                        if s.get("id") != scene.get("id")}
            errors = scenes_mod.validate_scene(scene, existing_ids=existing)
            if errors:
                self._json({"ok": False, "error": "；".join(errors)})
                return
            clean = scenes_mod.normalize_scene(scene)
            # 保留 last_launched（更新时不清零）
            old = next((s for s in scenes if s.get("id") == clean["id"]), None)
            if old and old.get("last_launched"):
                clean["last_launched"] = old["last_launched"]
            scenes = [s for s in scenes if s.get("id") != clean["id"]]
            if len(scenes) >= scenes_mod.MAX_SCENES:
                self._json({"ok": False,
                            "error": f"场景太多（上限 {scenes_mod.MAX_SCENES} 个），先删几个"})
                return
            scenes.append(clean)
            try:
                scenes_mod.save_scenes(scenes)
            except OSError as exc:
                self._json({"ok": False, "error": f"保存失败（磁盘写不进？）: {exc}"})
                return
            self._json({"ok": True, "id": clean["id"]})

        def _scene_action(self, action: str, data: dict) -> None:
            sid, _, sub = action.partition("/")
            scenes = scenes_mod.load_scenes()
            scene = next((s for s in scenes if s.get("id") == sid), None)
            if scene is None:
                self._error(404, "没有这个场景")
                return
            if sub == "remove":
                scenes_mod.save_scenes([s for s in scenes if s.get("id") != sid])
                self._json({"ok": True})
                return
            if sub == "duplicate":
                new_id = sid + "-copy"
                n = 2
                ids = {s.get("id") for s in scenes}
                while new_id in ids:
                    new_id = f"{sid}-copy{n}"
                    n += 1
                copy = dict(scene)
                copy["id"] = new_id
                copy["name"] = str(scene.get("name", ""))[:34] + " 副本"
                copy["last_launched"] = None
                scenes.append(copy)
                scenes_mod.save_scenes(scenes)
                self._json({"ok": True, "id": new_id})
                return
            if sub == "launch":
                if not scene.get("items"):
                    self._json({"ok": False, "error": "这个场景是空的——先编辑加几个条目"})
                    return
                by_id, manifests = scene_env()
                out = scenes_mod.launch_scene(
                    scene, by_id, launcher=launcher, runner=runner,
                    probe_port=probe_fn, plugin_alive=runner.alive,
                    plugin_manifests=manifests)
                session_id = uuid.uuid4().hex[:8]
                scene_sessions[session_id] = out["session"]
                scene["last_launched"] = time.time()
                scenes_mod.save_scenes(scenes)
                self._json({"ok": True, "session": session_id,
                            "results": out["results"],
                            "untracked": out["session"]["untracked"]})
                return
            self._error(404, "未知场景操作")

        def _scene_stop(self, data: dict) -> None:
            """只停"本次场景启动且可可靠识别"的项（见 scenes.py 设计说明）。"""
            session_id = str(data.get("session") or "")
            session = scene_sessions.get(session_id)
            if session is None:
                self._json({"ok": False,
                            "error": "找不到这次启动的记录（服务重启过？或已停止过）"})
                return
            stopped, skipped = [], []
            for handle in session.get("stoppable", []):
                try:
                    if handle["type"] == "tool_port":
                        killed = stopper(handle["port"])
                        (stopped if killed else skipped).append(handle.get("id", handle["port"]))
                    elif handle["type"] == "plugin":
                        killed = runner.stop(handle["id"])
                        (stopped if killed else skipped).append(handle["id"])
                except Exception:  # noqa: BLE001  单项停止失败不拖垮其余
                    skipped.append(handle.get("id", "?"))
            scene_sessions.pop(session_id, None)
            self._json({"ok": True, "stopped": stopped, "skipped": skipped,
                        "untracked": session.get("untracked", 0)})

        # ---------- 原有 ----------
        def _handle_settings(self, data: dict) -> None:
            if data.get("reset") is True:
                self._json({"settings": settings_mod.reset()})
                return
            valid_ids = {t.id for t in current_tools()}
            saved = settings_mod.save(data, valid_ids=valid_ids)
            self._json({"settings": saved})

        def _handle_launch(self, data: dict) -> None:
            tid = data.get("id")
            tool = {t.id: t for t in current_tools()}.get(tid) \
                if isinstance(tid, str) else None
            if tool is None:
                self._error(400, "未知工具 id")
                return
            if tool.kind == "plugin":
                m = plugins_mod.load_plugin(tool.id, runner.plugins_dir)
                if m is None:
                    self._json({"ok": False, "error": "插件已不存在"})
                    return
                try:
                    result = runner.launch(m, _sanitize_inputs(data.get("inputs")))
                except PluginError as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                self._json(result)
                return
            params = data.get("params") if isinstance(data.get("params"), dict) else {}
            try:
                result = launcher.launch(tool, params)
            except LaunchError as exc:
                self._json({"ok": False, "error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001 兜底：把异常翻译成人话
                self._json({"ok": False,
                            "error": f"启动失败（{type(exc).__name__}），请看日志或反馈该问题"})
                return
            self._json(result)

        def _handle_stop(self, data: dict) -> None:
            tid = data.get("id")
            tool = {t.id: t for t in current_tools()}.get(tid) \
                if isinstance(tid, str) else None
            if tool is None:
                self._error(400, "未知工具 id")
                return
            if tool.kind == "plugin":
                killed = runner.stop(tool.id)
                if killed:
                    self._json({"ok": True, "killed": killed})
                else:
                    self._json({"ok": False, "error": "它没有在运行（独立软件直接关它自己的窗口即可）"})
                return
            try:
                killed = stopper(tool.port)
            except OSError as exc:
                self._error(500, f"停止失败: {type(exc).__name__}")
                return
            launcher.forget(tool.id)
            if killed:
                self._json({"ok": True, "killed": killed})
            else:
                self._json({"ok": False, "error": "该工具没有在运行（或进程无法结束）"})

        # ---------- 代理 ----------
        def _handle_proxy(self, path: str) -> None:
            rest = path[len("/proxy/"):]
            tid, _, sub = rest.partition("/")
            tool = {t.id: t for t in current_tools()}.get(tid)
            if tool is None or not sub:
                self._error(404, "not found")
                return
            if not cfg_mod.path_allowed("/" + sub, tool.proxy) or ".." in sub:
                self._error(403, "路径不在该工具的透传白名单")
                return
            qs = urlparse(self.path).query
            target = "/" + sub + ("?" + qs if qs else "")
            try:
                res = proxy.fetch(tool.port, target)
            except proxy.UpstreamError as exc:
                self._error(502, f"上游不可达: {exc}")
                return
            extra = [("X-ToolHub-Truncated", "1")] if res.truncated else []
            self.send_response(res.status)
            self.send_header("Content-Type", res.content_type)
            self.send_header("Content-Length", str(len(res.body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(res.body)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
