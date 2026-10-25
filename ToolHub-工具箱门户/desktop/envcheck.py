"""环境自检：把"这台电脑能不能把每个工具用起来"翻译成一张人话清单。

每条检查 = {id, title, ok, level, detail, fix, tool}
- level: ok / warn / fail   （warn=能用但有注意点；fail=确认用不了）
- detail: 发现了什么（人话）
- fix:    下一步建议（人话，可空）
- tool:   归属工具 id（全局检查为 None）

设计约束：每条检查独立 try/except，任何一条异常都不拖垮整页；
重模块（onnxruntime/faster_whisper）导入结果缓存，避免每次点开都卡。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path

from . import WORKSPACE_ROOT, __version__
from .cfg import DesktopTool

_CACHE: dict[str, object] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 60.0


def _cached(key: str, fn):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        now = _now()
        if isinstance(hit, tuple) and hit[0] > now:
            return hit[1]
    val = fn()
    with _CACHE_LOCK:
        _CACHE[key] = (_now() + _CACHE_TTL, val)
    return val


def _now():
    import time
    return time.monotonic()


def _can_import(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _edge_or_webview2() -> tuple[bool, str]:
    """桌面窗口的渲染条件：pywebview（含 WebView2）或系统 Edge。"""
    if _can_import("webview"):
        return True, "pywebview 可用（Windows 用系统自带 WebView2 渲染）"
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    for base in (pf86, pf):
        exe = base / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if exe.is_file():
            return True, f"找到系统 Edge：{exe}"
    return False, "没找到 WebView2/Edge"


def check_python() -> dict:
    v = sys.version_info
    ok = v >= (3, 10)
    return {
        "id": "python", "title": "Python 运行时", "tool": None,
        "ok": ok, "level": "ok" if ok else "fail",
        "detail": f"当前 {v.major}.{v.minor}.{v.micro}（需要 3.10 及以上）",
        "fix": "" if ok else "安装 Python 3.10+，或使用自带运行时的安装包",
    }


def check_workspace_writable() -> dict:
    probe = WORKSPACE_ROOT / "ToolHub-工具箱门户" / "data"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        f = probe / ".write_probe"
        f.write_text("ok", encoding="utf-8")
        f.unlink()
        return {"id": "writable", "title": "数据目录可写", "tool": None, "ok": True,
                "level": "ok", "detail": "工具的数据库和日志都能正常落盘", "fix": ""}
    except OSError as exc:
        return {"id": "writable", "title": "数据目录可写", "tool": None, "ok": False,
                "level": "fail", "detail": f"写入失败：{exc}",
                "fix": "检查磁盘空间，或把整个文件夹移到有写权限的位置（不要放 C:\\Program Files）"}


def check_localcore() -> dict:
    try:
        root = str(WORKSPACE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import localcore.net  # noqa: F401
        return {"id": "localcore", "title": "共享件库 localcore", "tool": None,
                "ok": True, "level": "ok", "detail": "模块完整，工具间通信标准件就绪", "fix": ""}
    except Exception as exc:  # noqa: BLE001
        return {"id": "localcore", "title": "共享件库 localcore", "tool": None,
                "ok": False, "level": "fail", "detail": f"导入失败：{type(exc).__name__}",
                "fix": "确认 localcore 文件夹完整（重新解压安装包）"}


def check_window_shell() -> dict:
    ok, detail = _edge_or_webview2()
    return {"id": "shell", "title": "桌面窗口（WebView2/Edge）", "tool": None,
            "ok": ok, "level": "ok" if ok else "warn",
            "detail": detail,
            "fix": "" if ok else "升级 Windows 或安装 Edge；在此之前会退回默认浏览器打开界面"}


def check_laotu_ocr() -> dict:
    def run():
        return _can_import("rapidocr_onnxruntime")
    present = _cached("import:rapidocr", run)
    return {"id": "laotu-ocr", "title": "截图文字识别（OCR）引擎", "tool": "laotu",
            "ok": bool(present), "level": "ok" if present else "fail",
            "detail": ("识别引擎就绪（模型随程序自带，离线可用）" if present
                       else "缺少 rapidocr 组件"),
            "fix": "" if present else "用完整安装包重装，或在控制台执行 pip install rapidocr-onnxruntime"}


def check_bili_asr() -> dict:
    def run():
        return _can_import("faster_whisper")
    present = _cached("import:faster_whisper", run)

    def find_model():
        home = Path.home()
        for base in (home / ".cache" / "huggingface" / "hub",
                     Path(os.environ.get("HF_HUB_CACHE", home / ".cache" / "huggingface" / "hub"))):
            if base.is_dir() and any("faster-whisper" in p.name or "whisper" in p.name.lower()
                                     for p in base.iterdir()):
                return True
        return False
    model = _cached("model:whisper", find_model) if present else False
    if not present:
        return {"id": "bili-asr", "title": "语音转写引擎（消化器）", "tool": "bilidigest",
                "ok": False, "level": "fail", "detail": "缺少 faster-whisper 组件",
                "fix": "用完整安装包重装，或 pip install faster-whisper"}
    if model:
        return {"id": "bili-asr", "title": "语音转写引擎（消化器）", "tool": "bilidigest",
                "ok": True, "level": "ok", "detail": "引擎与转写模型就绪，完全离线可用", "fix": ""}
    return {"id": "bili-asr", "title": "语音转写引擎（消化器）", "tool": "bilidigest",
            "ok": True, "level": "warn",
            "detail": "引擎就绪，但本地还没有转写模型",
            "fix": "首次转写时会自动下载（约 150MB，需联网一次；下载完终身离线）"}


def check_launch_files(tools: tuple[DesktopTool, ...]) -> list[dict]:
    """每个工具的启动入口是否存在（cli.py / 模块目录）。"""
    out = []
    for t in tools:
        if t.source == "builtin" and t.id == "fileflow":
            ok = (t.dir_path / "fileflow" / "webui.py").is_file()
            miss = "fileflow/webui.py"
        else:
            ok = (t.dir_path / "cli.py").is_file()
            miss = "cli.py"
        out.append({
            "id": f"entry-{t.id}", "title": "启动入口", "tool": t.id,
            "ok": ok, "level": "ok" if ok else "fail",
            "detail": (f"启动文件就绪（{t.launch_dir}）" if ok
                       else f"找不到 {miss}（目录不完整？）"),
            "fix": "" if ok else "从完整安装包恢复该工具目录",
        })
    return out


def run_all(tools: tuple[DesktopTool, ...]) -> list[dict]:
    checks = [check_python(), check_workspace_writable(), check_localcore(),
              check_window_shell(), check_laotu_ocr(), check_bili_asr()]
    checks += check_launch_files(tools)
    return checks


def summarize(checks: list[dict]) -> dict:
    n_fail = sum(1 for c in checks if c["level"] == "fail")
    n_warn = sum(1 for c in checks if c["level"] == "warn")
    return {"checks": checks, "n_fail": n_fail, "n_warn": n_warn,
            "verdict": ("一切就绪" if not n_fail and not n_warn
                        else f"可用，但有 {n_warn} 个注意点" if not n_fail
                        else f"有 {n_fail} 个问题需要处理")}
