"""桌面版合并注册表：内置 10 工具 + tool.json 覆盖 + apps/ 目录扫描。

三层来源（后者覆盖前者同名 id）：
1. BUILTINS   —— 代码内声明（沿用 toolhub.registry 的端口/白名单数据，
                 补充分类、用途、输入/输出、参数、状态等展示元数据）
2. 项目根 tool.json —— 任何内置工具目录放一份 tool.json 即可覆盖元数据
3. apps/*/tool.json  —— 第三方工具：一个文件夹 + 一份配置即被识别，零代码

安全边界：id/port/argv 白名单校验；argv 只允许 {python} 占位；
launch 目录必须真实存在且位于工作区内；不合格条目跳过并记录 warning。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import WORKSPACE_ROOT
from toolhub.registry import path_allowed  # noqa: F401  （兼容再导出，代理白名单共用）

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
_STATUS = ("ready", "needs-setup", "experimental")


@dataclass(frozen=True)
class DesktopTool:
    id: str
    name: str
    desc: str
    icon: str
    category: str
    port: int | None = None             # None = 无端口（外部插件）
    launch_dir: str = ""                # 相对工作区根的目录（内置工具）
    argv: tuple[str, ...] = ()          # 含 {python} 占位；{param:x} 由启动器解析
    status: str = "ready"               # ready / needs-setup / experimental
    search: bool = False
    proxy: tuple[str, ...] = ()
    about_input: str = ""
    about_output: str = ""
    # 启动参数：argv 里 {param:x} 的取值（名称, 默认值），启动器强校验
    params: tuple[tuple[str, str], ...] = field(default=())
    # 展示参数：纯说明（名称, 描述），不参与启动
    about_params: tuple[tuple[str, str], ...] = field(default=())
    setup_hint: str = ""
    note: str = ""                       # 卡片上的实验性/开发中标注
    source: str = "builtin"              # builtin / tool.json / apps / plugin
    kind: str = "builtin"                # builtin | plugin
    icon_file: str = ""                  # 插件图标（相对插件目录的文件名）
    plugin_state: str = ""               # ok/invalid/disabled/draft（动态状态由 /api/status 刷新）
    needs_admin: bool = False            # 插件提权标记（卡片 🛡）
    aliases: tuple[str, ...] = ()        # 插件搜索别名

    @property
    def dir_path(self) -> Path:
        return WORKSPACE_ROOT / self.launch_dir if self.launch_dir else Path(".")


# {param:xxx} 占位校验（允许的参数名由 params 给出）
_PARAM_RE = re.compile(r"\{param:([a-z0-9_]+)\}")


def _validate(t: DesktopTool) -> str | None:
    """返回错误文案；None 表示合格。"""
    if not _ID_RE.match(t.id):
        return f"id 不合法: {t.id!r}（需小写字母/数字/连字符，2-40 位）"
    if not (1024 <= t.port <= 65535):
        return f"{t.id}: 端口 {t.port} 超出范围"
    if t.status not in _STATUS:
        return f"{t.id}: 未知状态 {t.status!r}"
    if not t.argv or "{python}" not in t.argv:
        return f"{t.id}: argv 必须包含 {{python}} 占位符"
    allowed = {name for name, _ in t.params}
    for a in t.argv:
        for m in _PARAM_RE.finditer(a):
            if m.group(1) not in allowed:
                return f"{t.id}: 参数占位 {m.group(0)} 未在 params 声明"
    d = t.dir_path
    try:
        d.resolve().relative_to(WORKSPACE_ROOT)
    except ValueError:
        return f"{t.id}: 启动目录 {t.launch_dir} 越出工作区"
    if not d.is_dir():
        return f"{t.id}: 启动目录不存在: {t.launch_dir}"
    return None


# ---------------------------------------------------------------------------
# 内置 10 工具（端口/白名单与 toolhub.registry 保持一致）
# ---------------------------------------------------------------------------
BUILTINS: tuple[DesktopTool, ...] = (
    DesktopTool(
        id="fileflow", name="FileFlow 文件整理器", icon="🧹", category="文件整理",
        desc="按规则自动整理文件夹：图片/文档/视频归类，先预览再执行，可撤销；重复文件三级查重",
        port=8635, launch_dir="FileFlow-文件整理器",
        argv=("{python}", "-m", "fileflow.webui", "8635"),
        about_input="选一个要整理的文件夹（如下载、桌面）",
        about_output="整理计划预览 → 确认后文件归类，随时一键撤销",
        about_params=(("目录", "启动后在网页界面里选择要整理的目录"),),
        proxy=("/api",),
    ),
    DesktopTool(
        id="landrop-share", name="LANDrop 局域网共享", icon="📡", category="传输共享",
        desc="把电脑里的文件夹共享给同一 Wi-Fi 的手机扫码下载",
        port=8800, launch_dir="LANDrop-lite-文件传输",
        argv=("{python}", "cli.py", "share", "{param:dir}"),
        params=(("dir", str(WORKSPACE_ROOT)),),
        about_input="要共享的文件夹路径",
        about_output="终端/日志里出现二维码和链接，手机浏览器打开即可下载",
        source="builtin",
    ),
    DesktopTool(
        id="landrop-receive", name="LANDrop 局域网接收", icon="📥", category="传输共享",
        desc="接收局域网设备推送的文件（手机 → 电脑），支持断点续传",
        port=8801, launch_dir="LANDrop-lite-文件传输",
        argv=("{python}", "cli.py", "receive", "--out", "{param:out}"),
        params=(("out", str(Path.home() / "LANDrop接收")),),
        about_input="无（启动后等手机推送）",
        about_output="手机上传的文件落到指定目录，日志里显示二维码与访问码",
        source="builtin",
    ),
    DesktopTool(
        id="laotu", name="捞图 截图记忆库", icon="🖼️", category="记忆搜索",
        desc="截图 OCR 入库，中文关键词半秒搜图 + 时间线 + 相似查重",
        port=8637, launch_dir="捞图-截图记忆库",
        argv=("{python}", "cli.py", "serve"),
        search=True, proxy=("/api", "/thumb", "/img"),
        about_input="无（自动索引系统截图目录；也可把别的图目录加进来）",
        about_output="输入中文关键词搜出含该文字的截图，点击看大图",
        status="ready",
        note="实验性：语义搜索（搜“含义”而不只是文字）开发中",
    ),
    DesktopTool(
        id="hukou", name="文件户口本 文件身世", icon="📔", category="记忆搜索",
        desc="回答这个文件从哪来：下载来源归因 + 改名/移动事件时间线",
        port=8638, launch_dir="文件户口本-文件身世",
        argv=("{python}", "cli.py", "serve"),
        search=True, proxy=("/api",),
        about_input="一个文件的完整路径（或先跑一次 scan 盘点下载夹）",
        about_output="身世卡：来源网址/微信接收？/事件时间线；deep 模式加谱系链",
        setup_hint="谱系链需要一次管理员控制台执行：python cli.py deep --history",
        status="ready",
    ),
    DesktopTool(
        id="bilidigest", name="B站收藏消化器", icon="📺", category="内容笔记",
        desc="视频转文字重点：本地语音转写 → 章节/关键词/重点句，零 API 费用；B站视频只下音频、转写完自动删",
        port=8639, launch_dir="B站收藏消化器-视频转重点",
        argv=("{python}", "cli.py", "serve"),
        proxy=("/api",),
        about_input="B站视频链接，或本地音视频文件路径",
        about_output="带时间戳的文字重点，点时间戳跳转视频对应位置",
        setup_hint="首次转写需联网下载语音模型（约 150MB，仅一次），之后完全离线",
        status="ready",
    ),
    DesktopTool(
        id="docline", name="文档户口本 文档谱系", icon="🌳", category="记忆搜索",
        desc="同一份文档的 N 个副本自动聚成谱系树：谁从谁改出来、哪份最新",
        port=8640, launch_dir="文档户口本-文档谱系",
        argv=("{python}", "cli.py", "serve"),
        search=False, proxy=("/api",),
        about_input="一个存满文档副本的目录（docx/pptx）",
        about_output="谱系树 + 任意两版逐段对比",
        source="builtin",
    ),
    DesktopTool(
        id="inspector", name="外发卫士 发前体检", icon="🛡️", category="隐私安全",
        desc="发文件前查隐私泄露：元数据/批注/GPS 定位，一键净化另存",
        port=8641, launch_dir="外发卫士-发前体检",
        argv=("{python}", "cli.py", "serve"),
        proxy=("/api",),
        about_input="要外发的文件（Word/PPT/Excel/图片/PDF/zip）",
        about_output="风险分级报告 + 净化后的干净副本（不覆盖原件）",
        source="builtin",
    ),
    DesktopTool(
        id="notenest", name="NoteNest 本地笔记", icon="📝", category="内容笔记",
        desc="纯本地 Markdown 笔记：实时预览、自动保存、全文搜索；截图可直接粘贴成图、选中文字一键上色",
        port=8890, launch_dir="NoteNest-本地笔记",
        argv=("{python}", "cli.py", "--port", "8890"),
        search=True, proxy=("/api",),
        about_input="直接开写；截图按 Ctrl+V 就能贴进笔记；笔记是普通 .md 文件（默认存 ~/NoteNest）",
        about_output="Markdown 文件（图片存同目录 assets/，Typora/VSCode 打开也能显示），任何编辑器都能打开",
        source="builtin",
    ),
    DesktopTool(
        id="clipvault", name="ClipVault 剪贴板时间线", icon="📋", category="记忆搜索",
        desc="剪贴板全记录：文本/图片/文件按时间线留存，可搜索、一键回贴",
        port=8643, launch_dir="ClipVault-剪贴板时间线",
        argv=("{python}", "cli.py", "serve", "--no-browser"),
        search=True, proxy=("/api",),
        about_input="无（后台自动记录复制内容）",
        about_output="可搜索的时间线；回贴=把历史条目放回剪贴板",
        source="builtin",
    ),
)


# ---------------------------------------------------------------------------
# tool.json 读取与合并
# ---------------------------------------------------------------------------
def _parse_tool_json(path: Path, source: str) -> tuple[DesktopTool | None, str | None]:
    """解析一份 tool.json。返回 (tool|None, warning|None)。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{path}: 读取失败（{type(exc).__name__}）"
    if not isinstance(data, dict):
        return None, f"{path}: 顶层必须是 JSON 对象"
    base = BUILTIN_BY_ID.get(data.get("id"))
    try:
        kwargs = dict(
            id=data["id"],
            name=str(data.get("name") or (base.name if base else data["id"])),
            desc=str(data.get("desc") or (base.desc if base else "")),
            icon=str(data.get("icon") or (base.icon if base else "🧰")),
            category=str(data.get("category") or (base.category if base else "其他")),
            port=int(data.get("port") or (base.port if base else 0)),
            launch_dir=str(data.get("launch_dir") or (
                base.launch_dir if base else path.parent.relative_to(WORKSPACE_ROOT).as_posix())),
            argv=tuple(data["launch"]["argv"]) if "launch" in data and "argv" in data["launch"]
            else (base.argv if base else ()),
            status=str(data.get("status") or (base.status if base else "ready")),
            search=bool(data.get("search", base.search if base else False)),
            proxy=tuple(data.get("proxy_prefixes", base.proxy if base else ())),
            about_input=str(data.get("about", {}).get("input") or (base.about_input if base else "")),
            about_output=str(data.get("about", {}).get("output") or (base.about_output if base else "")),
            # 启动参数：launch.params [{name, default}]（ argv 里 {param:x} 的来源）
            params=tuple(
                (str(p["name"]), str(p.get("default", "")))
                for p in (data.get("launch") or {}).get("params", [])
                if isinstance(p, dict) and p.get("name")
            ) or (base.params if base else ()),
            # 展示参数：about.params [{name, desc}]（纯说明）
            about_params=tuple(
                (str(p["name"]), str(p.get("desc", "")))
                for p in data.get("about", {}).get("params", [])
                if isinstance(p, dict) and p.get("name")
            ) or (base.about_params if base else ()),
            setup_hint=str(data.get("setup_hint") or (base.setup_hint if base else "")),
            note=str(data.get("note") or (base.note if base else "")),
            source=source,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"{path}: 字段缺失或类型不对（{type(exc).__name__}）"
    t = DesktopTool(**kwargs)
    err = _validate(t)
    if err:
        return None, f"{path}: {err}"
    return t, None


def _apps_scan(warnings: list[str]) -> list[DesktopTool]:
    out: list[DesktopTool] = []
    apps = WORKSPACE_ROOT / "apps"
    if not apps.is_dir():
        return out
    for d in sorted(apps.iterdir()):
        cfg_path = d / "tool.json"
        if not cfg_path.is_file():
            continue  # apps/ 下的普通文件夹不吭声地忽略
        t, warn = _parse_tool_json(cfg_path, "apps")
        if warn:
            warnings.append(warn)
        elif t:
            out.append(t)
    return out


BUILTIN_BY_ID: dict[str, DesktopTool] = {t.id: t for t in BUILTINS}


def load_tools() -> tuple[tuple[DesktopTool, ...], list[str]]:
    """合并四层来源。返回 (tools, warnings)；warnings 给设置页展示。"""
    warnings: list[str] = []
    by_id: dict[str, DesktopTool] = {t.id: t for t in BUILTINS}

    # 第二层：项目根 tool.json 覆盖内置
    for t in list(by_id.values()):
        cfg_path = t.dir_path / "tool.json"
        if cfg_path.is_file():
            override, warn = _parse_tool_json(cfg_path, "tool.json")
            if warn:
                warnings.append(warn)
            elif override and override.id == t.id:
                by_id[t.id] = override

    # 第三层：apps/ 新工具
    for t in _apps_scan(warnings):
        if t.id in by_id:
            warnings.append(f"apps/ 中 id 重复，已忽略: {t.id}")
            continue
        by_id[t.id] = t

    # 第四层：外部插件（data/plugins/*/plugin.json，enabled 的才进主界面）
    for m in _plugins_scan(warnings):
        if m.id in by_id:
            warnings.append(f"插件 id 与现有工具重复，已忽略: {m.id}")
            continue
        by_id[m.id] = m

    tools = tuple(by_id.values())
    # 内置在前，apps 次之，插件最后（稳定顺序）
    order = {"builtin": 0, "tool.json": 1, "apps": 2, "plugin": 3}
    tools = tuple(sorted(tools, key=lambda t: (order.get(t.source, 9), t.id)))
    return tools, warnings


def _plugins_scan(warnings: list[str]) -> list[DesktopTool]:
    """外部插件 → DesktopTool 视图。状态/图标等动态信息走 /api/status 与图标路由。"""
    from . import plugins as plugins_mod
    out: list[DesktopTool] = []
    for m in plugins_mod.list_plugins():
        state = m.get("_state") or {}
        if state.get("draft"):
            continue  # 草稿不进主界面（管理页可见）
        if state.get("enabled") is False:
            continue  # 停用的不进主界面
        display = m.get("display") or {}
        icon_emoji = {"exe": "🧩", "script": "📜", "store": "🛍", "url": "🌐",
                      "folder": "📁", "doc": "📄"}.get(m.get("kind"), "🧩")
        note = ""
        if (m.get("launch") or {}).get("elevate"):
            note = "需要管理员权限（启动时 Windows 会确认一次）"
        elif (m.get("trust") or {}).get("origin") == "shared":
            note = "来自别人分享的清单——首次启动前请确认目标程序无误"
        out.append(DesktopTool(
            id=m["id"],
            name=str(m.get("name") or m["id"]),
            desc=str(m.get("desc") or "（未填写说明——编辑插件可补一句，方便以后搜到）"),
            icon=icon_emoji,
            category=str(display.get("category") or "我的插件"),
            port=None,
            status="ready",
            search=False,
            proxy=(),
            source="plugin",
            kind="plugin",
            icon_file=str(m.get("icon") or "icon.png"),
            plugin_state=str(state.get("last_state") or "ok"),
            needs_admin=bool((m.get("launch") or {}).get("elevate")),
            note=note,
            aliases=tuple(str(x) for x in
                          (list(display.get("tags") or []) + list(display.get("aliases") or []))[:12]),
            about_input=str((m.get("input") or {}).get("accept") != "none"
                            and "启动时会让你选文件/填参数（参数模板）" or "无需输入，直接启动"),
        ))
    return out


def tool_view(t: DesktopTool, port_base: str = "") -> dict:
    """序列化给前端。"""
    view = {
        "id": t.id, "name": t.name, "desc": t.desc, "icon": t.icon,
        "category": t.category, "port": t.port, "status": t.status,
        "search": t.search, "note": t.note, "setup_hint": t.setup_hint,
        "source": t.source,
        "url": f"http://127.0.0.1:{t.port}/" if t.port else "",
        "about": {"input": t.about_input, "output": t.about_output,
                  "params": [{"name": n, "desc": d} for n, d in t.about_params]},
        "launch_params": [{"name": n, "default": d} for n, d in t.params],
    }
    if t.kind == "plugin":
        view["kind"] = "plugin"
        view["icon_url"] = f"/api/plugins/{t.id}/icon" if t.icon_file else ""
        view["needs_admin"] = t.needs_admin
        view["aliases"] = list(t.aliases)
    return view


def search_subset(tools: tuple[DesktopTool, ...]):
    """联邦搜索子集：鸭子类型即可（federated 只用 id/name/port）。"""
    return tuple(t for t in tools if t.search)
