"""外部插件（万物皆插件 · 阶段 A）：把电脑上任意第三方软件"注册"进工具箱。

三条铁律（docs/万物皆插件-设计稿.md 第 0 节）：
1. 只读原软件——导入/运行/卸载绝不写被导入软件目录；图标提取是复制画面副本到工具箱目录。
2. 清单无代码——plugin.json 是纯数据（严格字段白名单），工具箱只执行它指向的既有文件。
3. 进程隔离——第三方软件都是独立进程；"随工具箱退出"用 Job Object（仅 oneshot 默认开启）。

双文件：plugin.json（用户可编辑可分享）+ plugin.state.json（工具箱维护的统计）。
存放在工具箱自己的 data/plugins/<id>/ 下。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import WORKSPACE_ROOT

PLUGINS_DIR = WORKSPACE_ROOT / "ToolHub-工具箱门户" / "data" / "plugins"

KINDS = ("exe", "script", "store", "url", "folder", "doc")
MVP_KINDS = ("exe", "script", "store", "folder", "url", "doc")  # 阶段 C 二期：商店应用开放
DOC_EXE_SUFFIXES = (".exe", ".lnk")
SCRIPT_SUFFIXES = {".py": "python", ".pyw": "python", ".bat": "cmd",
                   ".cmd": "cmd", ".ps1": "powershell"}
STORE_PREFIX = "shell:appsfolder\\"   # UWP 统一入口：probe/launch 都用这个前缀 + AUMID

# 输入占位符（命令行参数模板）：设计稿 §6-1——替换后的整个值算一个 argv 元素，绝不二次分词
_PH_FILE = re.compile(r"\{file\}")
_PH_FOLDER = re.compile(r"\{folder\}")
_PH_TEXT = re.compile(r"\{text\}")
_PH_ARG = re.compile(r"\{arg:([^}]+)\}")
_PH_DERIVED = re.compile(r"\{(file_dir|file_stem)\}")

MANIFEST_VERSION = 1

# 严格字段白名单（未知字段一律拒绝——"清单无代码"红线的落地）
_TOP_KEYS = {"manifest_version", "id", "kind", "name", "desc", "version",
             "publisher", "source", "imported_at", "icon", "launch",
             "input", "output", "lifecycle", "display", "trust"}
_LAUNCH_KEYS = {"target", "workdir", "args", "env", "elevate", "window",
                "use_shell", "interpreter", "aumid"}
_INPUT_KEYS = {"accept", "via"}
_OUTPUT_KEYS = {"kind", "dir"}
_LIFECYCLE_KEYS = {"mode", "close_with_hub", "success", "timeout_sec"}
_DISPLAY_KEYS = {"category", "tags", "aliases", "pinned"}
_TRUST_KEYS = {"origin", "confirmed_launch", "resolved_from"}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


class PluginError(Exception):
    """人话版插件错误（信息直接展示给用户）。"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def expand_path(raw: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(raw or ""))).strip()


def slugify(name: str) -> str:
    """从名字生成 ascii slug；纯中文名退化为 plug-xxxx。"""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    s = re.sub(r"-{2,}", "-", s)[:30].strip("-")
    return s if _ID_RE.match(s) else "plug-" + uuid.uuid4().hex[:6]


def _read_json(p: Path) -> dict | None:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 校验（严格白名单）
# ---------------------------------------------------------------------------

def validate_manifest(d: dict) -> list[str]:
    errors: list[str] = []
    unknown = set(d) - _TOP_KEYS
    if unknown:
        errors.append(f"清单包含不允许的字段: {', '.join(sorted(unknown))}")
    if d.get("manifest_version") != MANIFEST_VERSION:
        errors.append("manifest_version 必须是 1")
    if not _ID_RE.match(str(d.get("id", ""))):
        errors.append("id 必须是小写字母/数字/连字符（2-40 位）")
    kind = d.get("kind")
    if kind not in KINDS:
        errors.append(f"kind 必须是 {'/'.join(KINDS)}")
    name = str(d.get("name", "")).strip()
    if not (1 <= len(name) <= 60):
        errors.append("名称需要 1-60 个字符")
    if len(str(d.get("desc", ""))) > 300:
        errors.append("说明过长（上限 300 字）")

    launch = d.get("launch")
    if not isinstance(launch, dict):
        errors.append("缺少 launch 配置")
    else:
        unknown = set(launch) - _LAUNCH_KEYS
        if unknown:
            errors.append(f"launch 包含不允许的字段: {', '.join(sorted(unknown))}")
        target = str(launch.get("target", "")).strip()
        if not target:
            errors.append("缺少 launch.target")
        if kind == "url" and not _SCHEME_RE.match(target):
            errors.append("URL 必须以协议开头（如 https://）")
        if kind == "store":
            aumid = str(launch.get("aumid", ""))
            if not aumid or "!" not in aumid:
                errors.append("商店应用必须提供 aumid（形如 包族名!入口名，从系统枚举获得）")
        if kind == "exe" and target:
            real = expand_path(target)
            if not real.lower().endswith(DOC_EXE_SUFFIXES):
                errors.append("exe 类型的目标必须是 .exe 或 .lnk 文件")
        args = launch.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            errors.append("args 必须是字符串数组")
        if launch.get("env") is not None and not isinstance(launch.get("env"), dict):
            errors.append("env 必须是对象")
        if str(launch.get("window", "normal")) not in ("normal", "minimized", "maximized", "hidden"):
            errors.append("window 取值不合法")
        interpreter = launch.get("interpreter")
        if interpreter is not None and interpreter not in ("python", "cmd", "powershell"):
            errors.append("interpreter 取值不合法")

    input_ = d.get("input") or {}
    if not isinstance(input_, dict) or (set(input_) - _INPUT_KEYS):
        errors.append("input 结构不合法")
    output = d.get("output") or {}
    if not isinstance(output, dict) or (set(output) - _OUTPUT_KEYS):
        errors.append("output 结构不合法")
    if output.get("kind") is not None and output.get("kind") not in ("window", "stdout", "directory"):
        errors.append("output.kind 取值不合法")
    life = d.get("lifecycle") or {}
    if not isinstance(life, dict) or (set(life) - _LIFECYCLE_KEYS):
        errors.append("lifecycle 结构不合法")
    if life.get("mode") is not None and life.get("mode") not in ("gui", "oneshot", "resident"):
        errors.append("lifecycle.mode 取值不合法")
    disp = d.get("display") or {}
    if not isinstance(disp, dict) or (set(disp) - _DISPLAY_KEYS):
        errors.append("display 结构不合法")
    trust = d.get("trust") or {}
    if not isinstance(trust, dict) or (set(trust) - _TRUST_KEYS):
        errors.append("trust 结构不合法")
    return errors


def fingerprint(d: dict) -> str:
    """去重指纹：展开环境变量+小写后的目标（store 用 AUMID，url 用整串）。"""
    kind = d.get("kind")
    launch = d.get("launch") or {}
    if kind == "store":
        return "store:" + str(launch.get("aumid", "")).lower()
    return kind + ":" + expand_path(launch.get("target", "")).lower().rstrip(". ")


# ---------------------------------------------------------------------------
# 存取
# ---------------------------------------------------------------------------

def plugin_dir(pid: str, plugins_dir: Path | None = None) -> Path:
    base = Path(plugins_dir) if plugins_dir else PLUGINS_DIR
    return base / pid


def load_plugin(pid: str, plugins_dir: Path | None = None) -> dict | None:
    d = plugin_dir(pid, plugins_dir)
    m = _read_json(d / "plugin.json")
    if m is None:
        return None
    m["_state"] = _read_json(d / "plugin.state.json") or {}
    return m


def save_plugin(manifest: dict, plugins_dir: Path | None = None,
                state: dict | None = None) -> Path:
    # 先剥离内部键（_state 等）再校验——load_plugin 的合并视图不回写
    clean = {k: v for k, v in manifest.items() if not k.startswith("_")}
    errors = validate_manifest(clean)
    if errors:
        raise PluginError("；".join(errors))
    d = plugin_dir(clean["id"], plugins_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    if state is not None:
        (d / "plugin.state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def set_state(pid: str, patch: dict, plugins_dir: Path | None = None) -> dict:
    d = plugin_dir(pid, plugins_dir)
    state = _read_json(d / "plugin.state.json") or {}
    state.update(patch)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def get_state(pid: str, plugins_dir: Path | None = None) -> dict:
    return _read_json(plugin_dir(pid, plugins_dir) / "plugin.state.json") or {}


def remove_plugin(pid: str, plugins_dir: Path | None = None) -> bool:
    d = plugin_dir(pid, plugins_dir)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def list_plugins(plugins_dir: Path | None = None) -> list[dict]:
    base = Path(plugins_dir) if plugins_dir else PLUGINS_DIR
    out: list[dict] = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        m = _read_json(d / "plugin.json")
        if m is None:
            continue
        m["_state"] = _read_json(d / "plugin.state.json") or {}
        out.append(m)
    return out


def find_duplicate(fp: str, plugins_dir: Path | None = None) -> dict | None:
    for m in list_plugins(plugins_dir):
        if fingerprint(m) == fp:
            return m
    return None


# ---------------------------------------------------------------------------
# 识别（probe）：类型判断 + 元数据 + 启动预览 + 图标提取
# ---------------------------------------------------------------------------

def _ps(script: str) -> dict[str, str]:
    """跑一段 PowerShell；期望 KEY=base64(UTF-8) 行（base64 免疫管道编码歧义，
    曾出现 UTF-8 双重编码的 Â® 乱码）。非 base64 行按原文兜底。"""
    cmd = ("powershell", "-NoProfile", "-Command",
           "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    import base64
    out: dict[str, str] = {}
    for ln in (r.stdout or "").splitlines():
        if "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        k = k.strip()
        try:
            out[k] = base64.b64decode(v.strip()).decode("utf-8")
        except Exception:  # noqa: BLE001  非 base64 行按原文保留
            out[k] = v.strip()
    return out


# PowerShell 侧把任意值转成 UTF-8 base64 的前缀（[string] 防 $null 报错）
_B64_HEAD = "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]"


def resolve_lnk(lnk_path: str) -> dict:
    """解析快捷方式（WScript.Shell COM）→ {target, args, workdir}。"""
    lit = str(lnk_path).replace("'", "''")
    b = _B64_HEAD
    out = _ps(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + lit + "');"
        "Write-Output ('TARGET='+ " + b + "$s.TargetPath)));"
        "Write-Output ('ARGS='+ " + b + "$s.Arguments)));"
        "Write-Output ('WORK='+ " + b + "$s.WorkingDirectory)));")
    return {"target": out.get("TARGET", ""), "args": out.get("ARGS", ""),
            "workdir": out.get("WORK", "")}


def exe_metadata(path: str) -> dict:
    lit = str(path).replace("'", "''")
    b = _B64_HEAD
    out = _ps(
        "$v=(Get-Item -LiteralPath '" + lit + "').VersionInfo;"
        "Write-Output ('NAME='+ " + b + "$v.ProductName)));"
        "Write-Output ('VER='+ " + b + "$v.FileVersion)));"
        "Write-Output ('PUB='+ " + b + "$v.CompanyName)));")
    return {"name": out.get("NAME", ""), "version": out.get("VER", ""),
            "publisher": out.get("PUB", "")}


def probe_path(raw: str, plugins_dir: Path | None = None,
               name_hint: str = "") -> dict:
    """向导第 2-3 步：识别类型、取元数据、生成启动预览、提取图标。

    支持：shell:appsfolder\\<AUMID>（商店应用，name_hint=显示名）/ http(s) URL /
    本地路径（exe/.lnk/脚本/文件夹/文档）。"""
    raw = str(raw or "").strip().strip('"')
    if not raw:
        raise PluginError("路径不能为空")

    if raw.lower().startswith(STORE_PREFIX):
        # 商店应用（UWP/MSIX）：shell:appsfolder\<AUMID>（阶段 C 二期，实机可行性已验证）
        aumid = raw[len(STORE_PREFIX):].strip()
        if not aumid or "!" not in aumid:
            raise PluginError("商店应用 ID 不合法（应形如 包族名!入口名）")
        manifest = {
            "manifest_version": 1, "id": slugify(str(name_hint or aumid.split("!")[0])),
            "kind": "store",
            "name": (str(name_hint or aumid.split("!")[0]))[:60], "desc": "",
            "launch": {"target": STORE_PREFIX + aumid, "workdir": "",
                       "args": [], "aumid": aumid, "use_shell": True},
            # 图标：WindowsApps 目录 ACL 拒绝普通进程读取 logo 文件（实测），
            # 用字母占位（与 folder/url/doc 同待遇），不假装能提取
            "output": {"kind": "window"},
        }
    elif _SCHEME_RE.match(raw) and "://" in raw:
        scheme = raw.split("://", 1)[0].lower()
        host = raw.split("://", 1)[1].split("/")[0]
        manifest = {
            "manifest_version": 1, "id": slugify(host or scheme), "kind": "url",
            "name": (host or scheme)[:60], "desc": "",
            "launch": {"target": raw, "workdir": "", "args": [], "use_shell": True},
        }
    else:
        p = Path(expand_path(raw))
        if not p.exists():
            raise PluginError(f"找不到这个路径：{p}")
        if p.is_dir():
            manifest = {
                "manifest_version": 1, "id": slugify(p.name), "kind": "folder",
                "name": p.name[:60], "desc": "",
                "launch": {"target": str(p), "workdir": "", "args": [], "use_shell": True},
            }
        elif p.suffix.lower() == ".lnk":
            manifest = _probe_lnk(p)
        elif p.suffix.lower() in (".py", ".pyw", ".bat", ".cmd", ".ps1"):
            interpreter = SCRIPT_SUFFIXES[p.suffix.lower()]
            manifest = {
                "manifest_version": 1, "id": slugify(p.stem), "kind": "script",
                "name": p.stem[:60], "desc": "",
                "launch": {"target": str(p), "workdir": str(p.parent), "args": [],
                           "interpreter": interpreter, "use_shell": False,
                           "window": "hidden"},
                # 脚本的默认生命周期：一次性任务（跑完退出即结果），隐藏窗口，退出码判定
                "lifecycle": {"mode": "oneshot", "close_with_hub": True,
                              "success": "exitcode", "timeout_sec": 0},
                "output": {"kind": "stdout"},
            }
        elif p.suffix.lower() == ".sh":
            raise PluginError("暂不支持 .sh 脚本（Windows 没有原生 bash）——"
                              "改写成 .bat/.ps1/.py 再导入")
        elif p.suffix.lower() == ".exe":
            meta = exe_metadata(str(p))
            manifest = {
                "manifest_version": 1, "id": slugify(meta.get("name") or p.stem),
                "kind": "exe",
                "name": (meta.get("name") or p.stem)[:60], "desc": "",
                "version": meta.get("version", ""),
                "publisher": meta.get("publisher", ""),
                "source": "manual",
                "launch": {"target": str(p), "workdir": str(p.parent),
                           "args": [], "use_shell": False},
            }
        else:
            manifest = {
                "manifest_version": 1, "id": slugify(p.stem), "kind": "doc",
                "name": p.stem[:60], "desc": "",
                "launch": {"target": str(p), "workdir": "", "args": [], "use_shell": True},
            }

    # 防御：schema 扩展前的新类型必须显式放行（当前 KINDS 已全部开放）
    if manifest["kind"] not in MVP_KINDS:
        raise PluginError(f"这一类（{manifest['kind']}）当前版本不支持导入")

    manifest.setdefault("input", {"accept": "none", "via": "argument"})
    manifest.setdefault("output", {"kind": "window"})
    manifest.setdefault("lifecycle", {"mode": "gui", "close_with_hub": False,
                                      "success": "alive_3s"})
    manifest.setdefault("display", {"category": "", "tags": [], "aliases": []})
    manifest.setdefault("trust", {"origin": "self", "confirmed_launch": False})
    errors = validate_manifest(manifest)

    # 图标提取（exe → 提取副本；其余 → 提交时生成字母占位）
    icon_tmp = ""
    if manifest["kind"] == "exe":
        tmp_dir = (Path(plugins_dir) if plugins_dir else PLUGINS_DIR) / "_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        icon_tmp = str(tmp_dir / ("probe-" + uuid.uuid4().hex[:8] + ".png"))
        if not extract_icon(manifest["launch"]["target"], icon_tmp):
            icon_tmp = ""

    fp = fingerprint(manifest)
    dup = find_duplicate(fp, plugins_dir)
    return {"manifest": manifest, "errors": errors, "fingerprint": fp,
            "duplicate": ({"id": dup["id"], "name": dup["name"]} if dup else None),
            "icon_tmp": icon_tmp,
            "kind_label": _KIND_LABEL.get(manifest["kind"], manifest["kind"])}


def _probe_lnk(p: Path) -> dict:
    lnk = resolve_lnk(str(p))
    target = lnk.get("target", "")
    if not target:
        raise PluginError("这个快捷方式解析不出目标程序（可能是商店应用，阶段 C 支持）")
    t = Path(target)
    base_trust = {"origin": "self", "confirmed_launch": False,
                  "resolved_from": str(p)}
    if t.suffix.lower() == ".exe":
        meta = exe_metadata(target)
        workdir = lnk.get("workdir") or str(t.parent)
        lnk_args = [a for a in lnk.get("args", "").split() if a]
        return {
            "manifest_version": 1, "id": slugify(t.stem), "kind": "exe",
            "name": (meta.get("name") or t.stem)[:60], "desc": "",
            "version": meta.get("version", ""),
            "publisher": meta.get("publisher", ""),
            "source": "lnk",
            "launch": {"target": target, "workdir": workdir,
                       "args": lnk_args, "use_shell": False},
            "trust": base_trust,
        }
    if t.is_dir():
        return {"manifest_version": 1, "id": slugify(t.name), "kind": "folder",
                "name": t.name[:60], "desc": "",
                "launch": {"target": str(t), "workdir": "", "args": [],
                           "use_shell": True},
                "trust": base_trust}
    return {"manifest_version": 1, "id": slugify(t.stem), "kind": "doc",
            "name": t.stem[:60], "desc": "",
            "launch": {"target": str(t), "workdir": "", "args": [],
                       "use_shell": True},
            "trust": base_trust}


_KIND_LABEL = {"exe": "程序（exe）", "script": "脚本", "store": "商店应用",
               "url": "网址", "folder": "文件夹", "doc": "文档"}


# ---------------------------------------------------------------------------
# 图标：提取副本 + 字母占位（绝不写回原文件）
# ---------------------------------------------------------------------------

def extract_icon(exe_path: str, out_png: str) -> bool:
    """从 exe 提取最大可用图标 → 归一化 64px PNG。失败返回 False。"""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    hicon = None
    for size in (256, 64, 48, 32):
        hicon = _extract_hicon(exe_path, size)
        if hicon:
            break
    if not hicon:
        return False
    img = _hicon_to_image(hicon)
    ctypes.WinDLL("user32").DestroyIcon(hicon)
    if img is None:
        return False
    _normalize_save(img, out_png)
    return True


def _extract_hicon(path: str, size: int):
    # 注意：PrivateExtractIcons 导出在 user32.dll（不在 shell32）
    user32 = ctypes.WinDLL("user32")
    user32.PrivateExtractIconsW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
        wintypes.UINT, wintypes.UINT]
    user32.PrivateExtractIconsW.restype = wintypes.UINT
    hicon = wintypes.HICON()
    got = user32.PrivateExtractIconsW(str(path), 0, size, size,
                                      ctypes.byref(hicon), None, 1, 0)
    if got and hicon:
        return hicon
    return None


def _hicon_to_image(hicon):
    from PIL import Image
    user32 = ctypes.WinDLL("user32")
    gdi32 = ctypes.WinDLL("gdi32")
    kernel32 = ctypes.WinDLL("kernel32")

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                    ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                    ("hbmColor", wintypes.HBITMAP)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                    ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                    ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                    ("bmBits", wintypes.LPVOID)]

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    ii = ICONINFO()
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HBITMAP]
    if not user32.GetIconInfo(hicon, ctypes.byref(ii)):
        return None
    try:
        if not ii.hbmColor:
            return None
        bm = BITMAP()
        if not gdi32.GetObjectW(ii.hbmColor, ctypes.sizeof(BITMAP), ctypes.byref(bm)):
            return None
        w, h = bm.bmWidth, bm.bmHeight
        if w <= 0 or h <= 0 or w > 1024 or h > 1024:
            return None
        buf = ctypes.create_string_buffer(w * h * 4)
        bi = BMIH()
        bi.biSize = ctypes.sizeof(BMIH)
        bi.biWidth = w
        bi.biHeight = -h          # top-down
        bi.biPlanes = 1
        bi.biBitCount = 32
        hdc = user32.GetDC(None)
        try:
            ok = gdi32.GetDIBits(hdc, ii.hbmColor, 0, h, buf, ctypes.byref(bi), 0)
        finally:
            user32.ReleaseDC(None, hdc)
        if not ok:
            return None
        img = Image.frombytes("RGBA", (w, h), buf.raw)
        r, g, b, a = img.split()
        return Image.merge("RGBA", (b, g, r, a))   # BGRA → RGBA
    finally:
        if ii.hbmColor:
            gdi32.DeleteObject(ii.hbmColor)
        if ii.hbmMask:
            gdi32.DeleteObject(ii.hbmMask)


_CATEGORY_COLORS = {"文件整理": (37, 99, 235), "记忆搜索": (124, 58, 237),
                    "内容笔记": (5, 150, 105), "隐私安全": (217, 119, 6),
                    "传输共享": (14, 116, 144), "专注效率": (225, 29, 72)}


def letter_icon(name: str, category: str, out_png: str) -> bool:
    """字母占位图标：分类色圆角方块 + 首字。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    color = _CATEGORY_COLORS.get(category, (71, 85, 105))
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=color + (255,))
    ch = (name or "?").strip()[0].upper()
    try:
        font = ImageFont.truetype("msyh.ttc", 30)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), ch, font=font)
    d.text((32 - (box[2] - box[0]) / 2 - box[0], 32 - (box[3] - box[1]) / 2 - box[1]),
           ch, fill=(255, 255, 255, 255), font=font)
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, "PNG")
    return True


def _normalize_save(img, out_png: str) -> None:
    from PIL import Image
    canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    side = max(img.size)
    scale = 56.0 / side if side > 56 else 1.0
    if scale < 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    canvas.paste(img, ((64 - img.width) // 2, (64 - img.height) // 2), img)
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png, "PNG")


# ---------------------------------------------------------------------------
# 参数模板：占位符展开（设计稿 §6-1/示例 2——ffmpeg 式命令行模板）
# ---------------------------------------------------------------------------

def expand_placeholders(raw_args: list[str], inputs: dict | None) -> list[str]:
    """把 {file}/{folder}/{text}/{arg:名}/{file_dir}/{file_stem} 替换成真实值。

    inputs = {"file": 路径, "folder": 路径, "text": 文本, "args": {名: 值}}。
    替换后的整个值仍是单个 argv 元素，绝不二次分词（空格/引号由 Windows
    参数传递规则统一处理）。缺什么占位符就报人话错。
    """
    inputs = inputs or {}
    file_v = str(inputs.get("file") or "")
    folder_v = str(inputs.get("folder") or "")
    text_v = inputs.get("text")
    arg_map = {str(k): str(v) for k, v in (inputs.get("args") or {}).items()}
    out: list[str] = []
    for raw in raw_args:
        v = str(raw)
        m = _PH_ARG.search(v)
        while m:
            name = m.group(1)
            if name not in arg_map:
                raise PluginError(f"参数里用了 {{arg:{name}}}，但启动面板里没有填它——填上再启动")
            v = v.replace(m.group(0), arg_map[name])
            m = _PH_ARG.search(v)
        if _PH_DERIVED.search(v):
            if not file_v:
                raise PluginError("参数里用了 {file_dir}/{file_stem}，但启动面板里没有选择文件")
            fp = Path(file_v)
            v = v.replace("{file_dir}", str(fp.parent)).replace("{file_stem}", fp.stem)
        if _PH_FILE.search(v):
            if not file_v:
                raise PluginError("参数里用了 {file}，但启动面板里没有选择文件——选好文件再启动")
            v = v.replace("{file}", file_v)   # str.replace：Windows 路径的 \ 不能进 re.sub 替换串
        if _PH_FOLDER.search(v):
            if not folder_v:
                raise PluginError("参数里用了 {folder}，但启动面板里没有选择文件夹")
            v = v.replace("{folder}", folder_v)
        if _PH_TEXT.search(v):
            if text_v is None:
                raise PluginError("参数里用了 {text}，但启动面板里没有填写文本")
            v = v.replace("{text}", str(text_v))
        out.append(v)
    return out


def required_inputs(manifest: dict) -> dict:
    """清单声明了哪些输入（给前端渲染启动表单）。
    返回 {"file": bool, "folder": bool, "text": bool, "args": [参数名...]}。
    参数名从 args 模板里的 {arg:名} 提取。"""
    launch = manifest.get("launch") or {}
    accept = str((manifest.get("input") or {}).get("accept") or "none")
    joined = " ".join(str(a) for a in (launch.get("args") or []))
    joined += str((manifest.get("output") or {}).get("dir") or "")
    args = sorted({m.group(1) for m in _PH_ARG.finditer(joined)})
    return {
        "file": "file" in accept and bool(_PH_FILE.search(joined)),
        "folder": "folder" in accept and bool(_PH_FOLDER.search(joined)),
        "text": bool(_PH_TEXT.search(joined)),
        "args": args,
    }


# ---------------------------------------------------------------------------
# 批量扫描（阶段 B）：绿色软件目录 → exe 候选（黑名单滤噪）
# ---------------------------------------------------------------------------

# 见设计稿 §6-9：绿色软件目录里遍地卸载器/安装器/更新器/崩溃处理器，见 exe 就收=垃圾列表
_SCAN_BLACKLIST = re.compile(
    r"^(unins\d*|unin\d*|setup[^_]*|install[^_]*|update|upgrade|crash|error|helper|"
    r"debug|_?gb(?!ase)|config|uninstall|patch|repair|eula|license|readme)", re.I)


def scan_dir(root: str, depth: int = 1) -> dict:
    """扫描一个目录找可导入的 exe/.lnk（顶层 + 一层深）。
    返回 {"items": [{path,name,size,mtime,blocked}], "scanned": N}。
    blocked=True 的仍返回（前端可显示"已过滤"），但默认不勾选。"""
    root_p = Path(expand_path(str(root or "")))
    if not root_p.is_dir():
        raise PluginError(f"这个目录不存在：{root_p}")
    items: list[dict] = []
    scanned = 0

    def visit(d: Path, level: int) -> None:
        nonlocal scanned
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for e in entries:
            scanned += 1
            try:
                if e.is_dir():
                    if level < depth and not e.name.startswith((".", "$")):
                        visit(e, level + 1)
                    continue
                if e.suffix.lower() not in DOC_EXE_SUFFIXES:
                    continue
                st = e.stat()
                items.append({
                    "path": str(e),
                    "name": e.name,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                    "blocked": bool(_SCAN_BLACKLIST.match(e.stem)),
                })
            except OSError:
                continue

    visit(root_p, 0)
    items.sort(key=lambda x: (x["blocked"], -x["size"]))
    return {"items": items, "scanned": scanned}


# ---------------------------------------------------------------------------
# 开始菜单（阶段 B）：枚举两处 Start Menu 的 .lnk，按文件夹分组
# ---------------------------------------------------------------------------

def startmenu_lnks() -> list[dict]:
    """返回 [{path, name, group}]，group=开始菜单里的子文件夹名（""=顶层）。
    只列文件名不解析（probe 阶段才解析目标），几百个 lnk 也是毫秒级。"""
    roots = [
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) /
        "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) /
        "Microsoft/Windows/Start Menu/Programs",
    ]
    out: list[dict] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.lnk")):
            rel = p.parent.relative_to(root)
            key = str(p).lower()
            if key in seen:
                continue          # 同一快捷方式在两处只留一份（用户级优先，先遍历的赢）
            seen.add(key)
            out.append({"path": str(p), "name": p.stem,
                        "group": str(rel) if str(rel) != "." else ""})
    out.sort(key=lambda x: (x["group"], x["name"].lower()))
    return out


def list_store_apps() -> list[dict]:
    """枚举系统商店应用（UWP/MSIX）→ [{aumid, name}]。

    数据源 Get-StartApps；AppID 含 "!" 是 UWP/MSIX 特征（普通 .lnk 不含）。
    实机验证（2026-10-25，Win11 26300）：197 条里 42 条；启动用
    shell:appsFolder\\<AUMID> 可靠（计算器实测 3 秒内拉起）。"""
    rc, out, err = _ps_raw(
        "Get-StartApps | Where-Object {$_.AppID -like '*!*'} | "
        "ForEach-Object { Write-Output ($_.Name + '|' + $_.AppID) }", timeout=30)
    if rc != 0:
        raise PluginError("枚举商店应用失败（PowerShell Get-StartApps 不可用）")
    apps = []
    seen = set()
    for ln in (out or "").splitlines():
        ln = ln.strip()
        if "|" not in ln:
            continue
        name, _, aumid = ln.partition("|")
        if not name or not aumid or aumid.lower() in seen:
            continue
        seen.add(aumid.lower())
        apps.append({"aumid": aumid, "name": name})
    apps.sort(key=lambda x: x["name"].lower())
    return apps


def _ps_raw(script: str, timeout: int = 30) -> tuple[int, str, str]:
    """跑一段 PowerShell 拿原始行输出（KEY|VALUE 简单行协议，不需要 base64）。"""
    r = subprocess.run(
        ("powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, r.stdout or "", r.stderr or ""


# ---------------------------------------------------------------------------
# 清单分享（阶段 C · 一期）：导出可分享文件 / 导入前预检 / 严格再校验
# ---------------------------------------------------------------------------

SHARE_FORMAT = "toolhub-plugin-share"
SHARE_VERSION = 1


def export_plugins(ids: list[str], plugins_dir: Path | None = None) -> dict:
    """把选中插件的清单导出为可分享结构。

    只含 plugin.json 白名单字段；图标二进制/运行状态/日志绝不进入分享文件
    （icon 置空——导入方按目标存在与否重新提取或用字母占位）。"""
    out: list[dict] = []
    for pid in ids:
        m = load_plugin(pid, plugins_dir)
        if m is None:
            raise PluginError(f"没有这个插件：{pid}")
        clean = {k: v for k, v in m.items() if not k.startswith("_")}
        clean["icon"] = ""            # 图标副本是本机文件，不进分享文本
        out.append(clean)
    return {"format": SHARE_FORMAT, "version": SHARE_VERSION,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "plugins": out}


def parse_share(text: str, plugins_dir: Path | None = None) -> dict:
    """解析并逐条预检分享文件（不落盘）。

    文件级问题（非 JSON / 缺 format 标记 / 版本不支持 / plugins 空）→ PluginError。
    条目级问题记在该条的 errors/conflict 里，由用户决定是否导入。"""
    try:
        data = json.loads(str(text or ""))
    except ValueError as exc:
        raise PluginError("这不是合法的 JSON 文件（复制时可能截断了）") from exc
    if not isinstance(data, dict) or data.get("format") != SHARE_FORMAT:
        raise PluginError('文件里缺少 "format": "toolhub-plugin-share" 标记——'
                          "不是 ToolHub 导出的分享文件")
    if data.get("version") != SHARE_VERSION:
        raise PluginError(f"分享文件版本 {data.get('version')!r} 不被支持"
                          f"（当前支持版本 {SHARE_VERSION}），请升级工具箱后再导入")
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        raise PluginError("分享文件里没有插件（plugins 为空）")
    if len(plugins) > 100:
        raise PluginError("分享文件里的插件太多（上限 100）")

    items: list[dict] = []
    for raw in plugins:
        entry = {"manifest": None, "errors": [], "exists": False, "conflict": None}
        if not isinstance(raw, dict):
            entry["errors"] = ["条目不是 JSON 对象"]
            items.append(entry)
            continue
        m = dict(raw)
        m["icon"] = ""                # 分享文件不携带图标；导入时重建
        errors = validate_manifest(m)   # 与手动导入完全同一条校验，不因来源是导出而豁免
        entry["manifest"] = m
        entry["errors"] = errors
        if not errors:
            target = expand_path((m.get("launch") or {}).get("target", ""))
            entry["exists"] = bool(target) and Path(target).exists()
            dup = find_duplicate(fingerprint(m), plugins_dir)
            if dup and dup["id"] != m.get("id"):
                entry["conflict"] = {"type": "duplicate",
                                     "id": dup["id"], "name": dup.get("name", "")}
            elif _plugin_id_taken(str(m.get("id", "")), plugins_dir):
                entry["conflict"] = {"type": "id_taken",
                                     "id": m["id"], "name": ""}
        items.append(entry)
    return {"items": items}


def _plugin_id_taken(pid: str, plugins_dir: Path | None) -> bool:
    if not pid:
        return False
    return (plugin_dir(pid, plugins_dir) / "plugin.json").is_file()


def import_plugins(manifests: list[dict], plugins_dir: Path | None = None,
                   rename: dict[str, str] | None = None) -> list[dict]:
    """导入（逐条独立，部分成功语义：某条失败不影响其他条）。

    rename = {原id: 新id}——用户对 ID 冲突选择的改名方案。
    每条：完整再校验 → 存盘（trust.origin=shared）→ exe 且目标存在则重新提取图标，
    否则字母占位。目标路径不存在不阻止导入：插件以"已失效"状态进入，
    走既有「重新定位」闭环恢复。"""
    rename = rename or {}
    results: list[dict] = []
    for raw in manifests:
        if not isinstance(raw, dict):
            results.append({"ok": False, "error": "条目不是 JSON 对象"})
            continue
        m = dict(raw)
        old_id = str(m.get("id", ""))
        new_id = str(rename.get(old_id, old_id))
        if new_id != old_id:
            if not _ID_RE.match(new_id):
                results.append({"ok": False, "id": old_id,
                                "error": f"新 ID {new_id!r} 不合法（小写字母/数字/连字符）"})
                continue
            m["id"] = new_id
        m["icon"] = ""
        m.setdefault("trust", {})
        m["trust"]["origin"] = "shared"
        m["trust"]["confirmed_launch"] = False
        m.setdefault("imported_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        errors = validate_manifest(m)
        if errors:
            results.append({"ok": False, "id": old_id, "error": "；".join(errors)})
            continue
        try:
            save_plugin(m, plugins_dir, state={"draft": False, "enabled": True})
        except PluginError as exc:
            results.append({"ok": False, "id": m["id"], "error": str(exc)})
            continue
        pdir = plugin_dir(m["id"], plugins_dir)
        target = expand_path((m.get("launch") or {}).get("target", ""))
        if m.get("kind") == "exe" and target and Path(target).exists():
            if not extract_icon(target, str(pdir / "icon.png")):
                letter_icon(m.get("name", ""), (m.get("display") or {})
                            .get("category", ""), str(pdir / "icon.png"))
        else:
            letter_icon(m.get("name", ""), (m.get("display") or {})
                        .get("category", ""), str(pdir / "icon.png"))
        results.append({"ok": True, "id": m["id"]})
    return results


# ---------------------------------------------------------------------------
# 运行器：独立进程 + 状态统计 + 日志
# ---------------------------------------------------------------------------

class PluginRunner:
    """每个插件独立进程；oneshot 跟踪退出码；gui 判 3 秒存活。"""

    def __init__(self, plugins_dir: Path | None = None):
        self.plugins_dir = Path(plugins_dir) if plugins_dir else PLUGINS_DIR
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # ---- 状态 ----
    def alive(self, pid: str) -> bool:
        with self._lock:
            p = self._procs.get(pid)
        return bool(p and p.poll() is None)

    def state_view(self, manifest: dict) -> dict:
        """给 /api/status 的插件状态（不发网络请求，只查文件与进程）。"""
        pid = manifest["id"]
        state = get_state(pid, self.plugins_dir)
        if state.get("draft"):
            return {"state": "draft", "running": self.alive(pid)}
        if state.get("enabled") is False:
            return {"state": "disabled", "running": False}
        kind = manifest.get("kind")
        if kind in ("url", "folder", "doc", "store"):
            return {"state": "ok", "running": self.alive(pid)}
        target = expand_path((manifest.get("launch") or {}).get("target", ""))
        if not target or not Path(target).exists():
            return {"state": "invalid", "running": False}
        return {"state": "ok", "running": self.alive(pid)}

    def stop(self, pid: str) -> list[int]:
        with self._lock:
            p = self._procs.pop(pid, None)
        if not p or p.poll() is not None:
            return []
        r = subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15)
        return [p.pid] if r.returncode == 0 else []

    # ---- 启动 ----
    def launch(self, manifest: dict, inputs: dict | None = None) -> dict:
        pid = manifest["id"]
        state = get_state(pid, self.plugins_dir)
        if state.get("draft"):
            raise PluginError("这个插件还是草稿，先在导入向导里完成它")
        if state.get("enabled") is False:
            raise PluginError("该插件已停用（在「我的插件」里可以重新启用）")
        if self.alive(pid):
            return {"ok": True, "already": True, "hint": "它已经在运行了"}

        launch = manifest.get("launch") or {}
        kind = manifest.get("kind")
        target = expand_path(launch.get("target", ""))

        # 阶段 B 参数模板：占位符在这里统一展开（缺输入=人话报错，见 expand_placeholders）
        raw_args = expand_placeholders(list(launch.get("args") or []), inputs)

        workdir = expand_path(
            str(launch.get("workdir") or "{target_dir}").replace(
                "{target_dir}", str(Path(target).parent)))
        window = str(launch.get("window", "normal"))

        log_file = plugin_dir(pid, self.plugins_dir) / "log.txt"
        self._log(log_file, "启动 {k}: {t} 参数={a} 工作目录={w}".format(
            k=kind, t=target, a=raw_args or "无", w=workdir or "默认"))

        if kind in ("url", "doc") or (kind == "folder" and launch.get("use_shell")):
            try:
                os.startfile(target)  # noqa: S606  交给系统默认处理程序
            except OSError as exc:
                raise PluginError(f"系统拒绝打开：{exc}") from exc
            self._after_launch(pid)
            return {"ok": True, "hint": "已交给系统打开"}

        if kind == "folder":
            subprocess.Popen(["explorer.exe", target])
            self._after_launch(pid)
            return {"ok": True, "hint": "已在文件资源管理器中打开"}

        if kind == "store":
            aumid = str(launch.get("aumid") or "")
            if not aumid:
                raise PluginError("缺少商店应用 ID（AppUserModelID）")
            # target 与 probe 统一（shell:appsfolder\<AUMID>），单源避免两处拼接漂移
            os.startfile(target or (STORE_PREFIX + aumid))  # noqa: S606
            self._after_launch(pid)
            return {"ok": True, "hint": "已启动商店应用"}

        if kind == "script":
            argv = self._script_argv(manifest, target, raw_args)
        elif kind == "exe":
            if not target.lower().endswith(DOC_EXE_SUFFIXES):
                raise PluginError(f"目标不是可执行文件：{target}")
            if not Path(target).exists():
                raise PluginError(
                    f"找不到文件（可能被移动或卸载了）：{target}。"
                    "到「我的插件」里点「重新定位」选它的新位置")
            argv = [target] + [expand_path(a) for a in raw_args]
        else:
            raise PluginError(f"这一类（{kind}）当前版本不支持启动")

        if launch.get("elevate"):
            self._elevate(target, raw_args, workdir)
            self._after_launch(pid)
            return {"ok": True, "hint": "已请求管理员权限启动（Windows 会弹一次确认窗）"}

        si = subprocess.STARTUPINFO()
        si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        si.wShowWindow = {"normal": 1, "maximized": 3, "minimized": 6,
                          "hidden": 0}.get(window, 1)
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (launch.get("env") or {}).items()})
        try:
            proc = subprocess.Popen(
                argv, cwd=workdir if Path(workdir).is_dir() else None,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                startupinfo=si, env=env)
        except OSError as exc:
            raise PluginError(f"启动失败：{exc}") from exc

        with self._lock:
            self._procs[pid] = proc
        if (manifest.get("lifecycle") or {}).get("close_with_hub"):
            threading.Thread(target=self._attach_job, args=(proc,),
                             daemon=True).start()
        threading.Thread(target=self._watch,
                         args=(pid, manifest, proc, log_file),
                         daemon=True).start()
        self._after_launch(pid)
        result = {"ok": True, "pid": proc.pid, "hint": "已启动（几秒后状态会自动刷新）"}
        # 输出目录（output.kind=directory 时前端显示"打开输出文件夹"按钮）
        output = manifest.get("output") or {}
        if output.get("kind") == "directory" and str(output.get("dir") or ""):
            try:
                out_dir = expand_placeholders([str(output["dir"])], inputs)[0]
            except PluginError:
                out_dir = str(output["dir"])
            result["output_dir"] = out_dir
        return result

    def _script_argv(self, manifest: dict, target: str,
                     raw_args: list[str]) -> list[str]:
        interp = (manifest.get("launch") or {}).get("interpreter") or "python"
        if interp == "cmd":
            return ["cmd", "/c", target] + raw_args
        if interp == "powershell":
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", target] + raw_args
        py = shutil.which("python")
        if not py:
            bundled = WORKSPACE_ROOT.parent / "runtime" / "python.exe"
            if bundled.is_file():
                py = str(bundled)
            else:
                raise PluginError("电脑上没有 Python，脚本跑不了。装 Python 或改用完整安装包")
        return [py, target] + [expand_path(a) for a in raw_args]

    def _elevate(self, target: str, args: list[str], workdir: str) -> None:
        shell32 = ctypes.WinDLL("shell32")
        shell32.ShellExecuteW.argtypes = [
            wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        params = " ".join('"' + a + '"' for a in args)
        rc = shell32.ShellExecuteW(None, "runas", target, params,
                                   workdir or None, 1)  # SW_SHOWNORMAL
        if not rc or rc <= 32:
            raise PluginError("Windows 拒绝了提权请求（可能你在 UAC 弹窗里点了取消）")

    def _attach_job(self, proc: subprocess.Popen) -> None:
        """把子进程挂进"工具箱退出即终止"的 Job。失败仅放弃该特性。"""
        try:
            kernel32 = ctypes.WinDLL("kernel32")

            class BASIC_LIMIT(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in
                            ("ReadOperationCount", "WriteOperationCount",
                             "OtherOperationCount", "ReadTransferCount",
                             "WriteTransferCount", "OtherTransferCount")]

            class EXT_LIMIT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC_LIMIT),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            hjob = kernel32.CreateJobObjectW(None, None)
            if not hjob:
                return
            kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            kernel32.AssignProcessToJobObject.argtypes = [
                wintypes.HANDLE, wintypes.HANDLE]
            info = EXT_LIMIT()
            info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(hjob, 9, ctypes.byref(info),
                                             ctypes.sizeof(info))
            kernel32.AssignProcessToJobObject(hjob, wintypes.HANDLE(proc.handle))
        except Exception:  # noqa: BLE001
            pass

    def _watch(self, pid: str, manifest: dict, proc: subprocess.Popen,
               log_file: Path) -> None:
        """后台跟踪：oneshot 记退出码；全部写日志；退出后移出跟踪表。
        timeout_sec>0 时超时强杀（命令行工具卡死保护，设计稿 §8-14）。"""
        mode = (manifest.get("lifecycle") or {}).get("mode", "gui")
        timeout = int((manifest.get("lifecycle") or {}).get("timeout_sec") or 0)
        timer = None
        if timeout > 0:
            def _kill_timeout() -> None:
                if proc.poll() is None:
                    self._log(log_file, f"超过 {timeout} 秒没有结束，按超时强制终止")
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=15)
            timer = threading.Timer(timeout, _kill_timeout)
            timer.daemon = True
            timer.start()
        code = -1
        try:
            with log_file.open("a", encoding="utf-8", errors="replace") as f:
                if proc.stdout is not None:
                    for line in proc.stdout:
                        f.write(line if line.endswith("\n") else line + "\n")
                code = proc.wait()
                f.write(f"----- 进程退出（代码 {code}）-----\n")
        except OSError:
            pass
        finally:
            if timer is not None:
                timer.cancel()
            with self._lock:
                self._procs.pop(pid, None)
        if mode == "oneshot":
            set_state(pid, {"last_exit": code, "last_ok": code == 0},
                      self.plugins_dir)

    def _after_launch(self, pid: str) -> None:
        state = get_state(pid, self.plugins_dir)
        state["last_launched"] = time.time()
        state["run_count"] = int(state.get("run_count") or 0) + 1
        state["last_checked"] = time.time()
        set_state(pid, state, self.plugins_dir)

    @staticmethod
    def _log(log_file: Path, msg: str) -> None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except OSError:
            pass
