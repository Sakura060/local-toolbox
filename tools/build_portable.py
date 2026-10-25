# -*- coding: utf-8 -*-
"""构建 ToolHub 桌面版可移植包：自带 Python 运行时 + 全部依赖 + 预置模型。

产物（可重复执行，每次重建）：
  发布包-桌面版/ToolHub桌面版/          便携目录（复制到任何 Win10/11 电脑即用）
    AppToolHub/runtime/                 Python 3.13 embeddable + site-packages
    AppToolHub/workspace/               工作区源码（排除全部个人数据与缓存）
    AppToolHub/models/hf/               预置 faster-whisper base（消化器离线可用）
    启动工具箱.bat / 启动工具箱（无窗口）.bat / 使用说明.txt
  发布包-桌面版/ToolHub桌面版-便携版.zip  上面内容的压缩包
  发布包-桌面版/inno/ToolHub桌面版.iss    安装包脚本（用 Inno Setup 编译）

铁律：
- 构建产物里绝不包含用户个人数据（data/、*.db、缩略图、转写稿、日志全排除）
- 脱敏断言失败立即非零退出（同 tools/make_release.py 的纪律）
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "发布包-桌面版"


def _read_version() -> str:
    """版本号唯一来源：desktop/__init__.py（与 _app_version 同源）。"""
    init = (ROOT / "ToolHub-工具箱门户" / "desktop" / "__init__.py").read_text(
        encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if not m:
        raise SystemExit("读不到 desktop/__init__.py 的 __version__——拒绝用猜测的版本号打包")
    return m.group(1)


VERSION = _read_version()
# 版本化输出（2026-10-25 用户要求）：每个版本的产物进独立子文件夹 v<版本>/，
# 新版本构建永不覆盖旧版本；runtime 缓存（476MB，版本无关）留在共享目录幂等复用。
VDIR = BUILD / ("v" + VERSION)
OUT = VDIR / "ToolHub桌面版"
APP = OUT / "AppToolHub"
RUNTIME = BUILD / "_runtime"          # 共享缓存：stage_runtime 在这里幂等复用
OUT_RUNTIME = APP / "runtime"         # 构建产物里的 runtime（从缓存复制）
WS = APP / "workspace"
MODELS = APP / "models" / "hf"

PY_EMBED_PATH = "3.13.2/python-3.13.2-embed-amd64.zip"
# 直连 python.org 在部分网络环境会慢滴流卡死（同 HF 的教训）：镜像优先
PY_EMBED_URLS = [
    f"https://mirrors.huaweicloud.com/python/{PY_EMBED_PATH}",
    f"https://registry.npmmirror.com/-/binary/python/{PY_EMBED_PATH}",
    f"https://www.python.org/ftp/python/{PY_EMBED_PATH}",
]
PY_EMBED_SHA256 = ""  # 下载后现场计算并打印；首版不预置（对官网 TLS 的信任 + 冒烟验证兜底）

SITE_PACKAGES = [
    "pillow", "pystray", "pywebview", "numpy", "jieba",
    "rapidocr-onnxruntime", "faster-whisper",
]

# 排除目录名（任何层级）
EXCLUDE_DIRS = {
    "__pycache__", ".git", ".mimosa", ".v2c", ".video_agent", ".zcode",
    "发布包-GitHub", "发布包-桌面版", "node_modules", ".idea", ".vscode",
    "edge-profile", "ocr_batches", "data",
}
# 排除文件后缀
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".db", ".db-shm", ".db-wal",
                  ".sqlite3", ".pyd.orig")
# 排除文件名
EXCLUDE_NAMES = {"tests_last_probe.py", ".zcodeignore", "desktop.json",
                 "tray.log", "ocr_trace.log", "worker_trace.log"}


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f}{u}"
        n /= 1024


def dir_size(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
def stage_runtime() -> None:
    """1) Python embeddable + 2) pip --target 依赖。

    关键修复：embeddable 发行版的 python3XX._pth 会启用隔离模式——
    脚本所在目录不再自动进 sys.path，工具的 cli.py 将找不到自己的包。
    删除 ._pth 恢复标准路径解析（stdlib 仍来自 python313.zip，
    site-packages 由 site.py 按 prefix=exe目录 补上）。
    """
    zip_path = BUILD / "python-embed.zip"
    BUILD.mkdir(parents=True, exist_ok=True)
    def _zip_ok(p: Path) -> bool:
        try:
            with zipfile.ZipFile(p) as z:
                return len(z.namelist()) > 10
        except Exception:  # noqa: BLE001
            return False

    if zip_path.exists() and not _zip_ok(zip_path):
        zip_path.unlink()  # 上次失败留下的残件
    if not zip_path.exists():
        import socket
        socket.setdefaulttimeout(30)
        last_err: Exception | None = None
        for url in PY_EMBED_URLS:
            try:
                log(f"[1a] 下载 embeddable Python: {url}")
                urllib.request.urlretrieve(url, zip_path)
                if _zip_ok(zip_path):
                    break
                raise OSError(f"下载不完整或非 zip: {zip_path.stat().st_size}B")
            except Exception as exc:  # noqa: BLE001  逐源降级
                last_err = exc
                if zip_path.exists():
                    zip_path.unlink()
                log(f"[1a] 该源失败（{type(exc).__name__}），换下一个…")
        else:
            raise SystemExit(f"全部下载源失败: {last_err}")
        sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        log(f"[1a] sha256={sha[:16]}… size={human(zip_path.stat().st_size)}")

    # 幂等护栏（必须在 rmtree 之前）：runtime 完整且依赖齐全 → 只清 ._pth 就复用
    sp = RUNTIME / "Lib" / "site-packages"
    marker = [sp / "PIL", sp / "pystray", sp / "webview", sp / "numpy",
              sp / "faster_whisper", sp / "rapidocr_onnxruntime"]
    if RUNTIME.joinpath("python.exe").is_file() and all(m.exists() for m in marker):
        for pth in RUNTIME.glob("python*._pth"):
            pth.unlink()
        log(f"[1] runtime 幂等复用（{human(dir_size(RUNTIME))}，._pth 已确认移除）")
        return

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(RUNTIME)
    # 脱敏断言（防 zip-slip）：解包后的每个文件必须仍在 runtime 目录内
    runtime_resolved = RUNTIME.resolve()
    for f in RUNTIME.rglob("*"):
        f.resolve().relative_to(runtime_resolved)  # 越界会抛 ValueError
    # 关键：删除 ._pth，恢复标准 sys.path 行为（脚本目录可用、site 生效）
    for pth in RUNTIME.glob("python*._pth"):
        pth.unlink()
    log(f"[1b] runtime 就绪（{human(dir_size(RUNTIME))}，._pth 已移除）")

    log(f"[1c] pip --target 安装依赖: {' '.join(SITE_PACKAGES)}")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--target", str(sp),
         "--upgrade", "--no-cache-dir", "--quiet", *SITE_PACKAGES],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        log(r.stdout[-2000:])
        log(r.stderr[-2000:])
        raise SystemExit("pip 安装失败")
    log(f"[1c] 依赖就绪（{human(dir_size(RUNTIME))}）")


def copy_workspace() -> None:
    """3) 工作区复制（白名单：只带工具箱自身 + 内置工具 + 共享件，排除个人数据/创作产物）。

    曾经用黑名单排除——2026-10-25 复盘：mv-huidaoguoqu/操作者肖像两个创作工程
    （共 1.5GB）无声打进发布包，zip 从 333MB 涨到 1.7GB。黑名单永远追不上
    工作区长新东西，改为白名单：内置工具目录直接读 desktop.cfg.BUILTINS，
    未来新增工具自动跟随；不在白名单里的目录（创作产物/发布包/新实验）一概不进。"""
    if WS.exists():
        shutil.rmtree(WS)
    WS.mkdir(parents=True)

    # 白名单目录：工具箱 + 共享件 + 工作区工具 + 内置工具（读注册表，自动跟随）
    toolhub_dir = ROOT / "ToolHub-工具箱门户"
    sys.path.insert(0, str(toolhub_dir))
    from desktop.cfg import BUILTINS  # noqa: E402  打包机即开发机，直接读注册表
    include_dirs = {"ToolHub-工具箱门户", "localcore", "tools", "apps"}
    include_dirs.update(t.launch_dir for t in BUILTINS if t.launch_dir)
    # 根层只带说明文档
    include_files = {"README.md"}

    n_files = 0
    for src in ROOT.iterdir():
        name = src.name
        if src.is_file():
            if name not in include_files:
                continue
            shutil.copy2(src, WS / name)
            n_files += 1
            continue
        if name not in include_dirs:
            continue
        shutil.copytree(
            src, WS / name,
            ignore=shutil.ignore_patterns(
                *(list(EXCLUDE_DIRS) + [f"*{s}" for s in EXCLUDE_SUFFIX]
                  + list(EXCLUDE_NAMES))),
        )
    # 脱敏断言：产物里不允许出现数据库/日志/个人数据
    leaked = []
    for f in WS.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() in (".db", ".sqlite3", ".log") or f.name in EXCLUDE_NAMES:
            leaked.append(str(f.relative_to(WS)))
    if leaked:
        for x in leaked[:20]:
            log(f"  泄露: {x}")
        raise SystemExit(f"脱敏断言失败：{len(leaked)} 个文件不允许进安装包")
    size = dir_size(WS)
    # 体积护栏：纯工具树应 <200MB；超限说明又卷进了不该带的东西（创作产物/模型）
    if size > 200 * 1024 * 1024:
        big = sorted(((sum(f2.stat().st_size for f2 in d2.rglob("*") if f2.is_file()), d2.name)
                      for d2 in WS.iterdir() if d2.is_dir()), reverse=True)[:5]
        for s, nm in big:
            log(f"  大头: {human(s)}  {nm}")
        raise SystemExit(f"体积护栏：workspace 复制后 {human(size)} 超 200MB 上限，检查白名单")
    log(f"[2] 工作区复制完成（{n_files} 个根文件 + {len(include_dirs)} 个目录白名单，"
        f"{human(size)}，脱敏断言通过）")


def stage_models() -> None:
    """4) 预置 faster-whisper base 模型（保留 HF hub 目录布局）。"""
    src = Path.home() / ".cache" / "huggingface" / "hub" / "models--Systran--faster-whisper-base"
    if not src.is_dir():
        raise SystemExit("找不到本机 whisper 模型缓存")
    if MODELS.exists():
        shutil.rmtree(MODELS)
    MODELS.mkdir(parents=True)
    dst = MODELS / src.name
    shutil.copytree(src, dst,
                    ignore=shutil.ignore_patterns(".lock", "*.lock"))
    log(f"[3] 模型预置完成（{human(dir_size(MODELS))}）")


BATS = {
    # 正文纯 ASCII（D13 铁律）：中文项目目录用通配符 ToolHub-* 命中，中文不进 bat
    "启动工具箱.bat": """@echo off
rem ToolHub Desktop launcher (bundled runtime, with console for logs)
cd /d "%~dp0AppToolHub\\workspace"
cd /d "ToolHub-*"
set "TOOLHUB_PYTHON=%~dp0AppToolHub\\runtime\\python.exe"
set "HF_HUB_CACHE=%~dp0AppToolHub\\models\\hf"
set "HF_HOME=%~dp0AppToolHub\\models\\hf"
echo Starting ToolHub Desktop ...
"%TOOLHUB_PYTHON%" cli.py desktop
echo.
echo Desktop exited.
pause
""",
    "启动工具箱（无窗口）.bat": """@echo off
rem ToolHub Desktop launcher (no console window, native window only)
cd /d "%~dp0AppToolHub\\workspace"
cd /d "ToolHub-*"
set "TOOLHUB_PYTHON=%~dp0AppToolHub\\runtime\\pythonw.exe"
set "HF_HUB_CACHE=%~dp0AppToolHub\\models\\hf"
set "HF_HOME=%~dp0AppToolHub\\models\\hf"
start "ToolHub" "%TOOLHUB_PYTHON%" cli.py desktop
""",
}


def stage_launchers() -> None:
    for name, body in BATS.items():
        p = OUT / name
        p.write_bytes(body.replace("\n", "\r\n").encode("ascii"))
    readme = OUT / "使用说明.txt"
    readme.write_text(
        "ToolHub 桌面版 —— 使用说明\n"
        "================================\n\n"
        "【最快上手】双击「启动工具箱.bat」，会出现工具箱窗口。\n"
        "（黑窗口是日志，最小化即可，别关；关了窗口会一起退出。\n"
        "  想只要窗口不要黑框：用「启动工具箱（无窗口）.bat」）\n\n"
        "【放到别的电脑】把这整个文件夹复制过去（U盘/网盘都行），\n"
        "在那台电脑上双击「启动工具箱.bat」——不需要安装 Python，不需要联网。\n\n"
        "【第一次用】窗口里会提示跑一遍「环境自检」，照着做就行。\n\n"
        "【数据在哪】所有工具的数据都存在本文件夹 workspace\\ 各项目里，\n"
        "把文件夹整个拷走 = 数据一起带走；删除文件夹 = 不留痕迹。\n\n"
        "【常见问题】见 workspace\\ToolHub-工具箱门户\\docs\\ 下的三份文档。\n",
        encoding="utf-8",
    )
    log("[4] 启动器与说明就绪")


def stage_zip() -> None:
    zip_path = VDIR / "ToolHub桌面版-便携版.zip"
    if zip_path.exists():
        zip_path.unlink()
    log("[5] 压缩中（几分钟）…")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in OUT.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(OUT))
    log(f"[5] {zip_path.name}（{human(zip_path.stat().st_size)}）")


# （原 _app_version() 已上移为模块级 VERSION：版本化输出目录需要在导入期就知道版本号）


def write_inno_script() -> None:
    iss = VDIR / "inno" / "ToolHub桌面版.iss"
    iss.parent.mkdir(parents=True, exist_ok=True)
    iss.write_text(f"""; Inno Setup 脚本 —— ToolHub 桌面版安装包
; 编译：ISCC.exe ToolHub桌面版.iss（Inno Setup 6）
; AppVersion 由 _read_version() 从 desktop/__init__.py 注入，勿手改

[Setup]
AppName=ToolHub 工具箱
AppVersion={VERSION}
AppPublisher=ToolHub Desktop
DefaultDirName={{localappdata}}\\ToolHub
DefaultGroupName=ToolHub 工具箱
PrivilegesRequired=lowest
Compression=lzma2/max
SolidCompression=yes
OutputDir={VDIR.as_posix()}
OutputBaseFilename=ToolHub桌面版-安装包
SetupIconFile={ (ROOT / "ToolHub-工具箱门户" / "docs" / "app.ico").as_posix() }
UninstallDisplayIcon={{app}}\\AppToolHub\\runtime\\pythonw.exe

[Files]
Source: "{OUT.as_posix()}\\*"; DestDir: "{{app}}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\ToolHub 工具箱"; Filename: "{{app}}\\AppToolHub\\runtime\\pythonw.exe"; Parameters: "cli.py desktop"; WorkingDir: "{{app}}\\AppToolHub\\workspace\\ToolHub-工具箱门户"
Name: "{{group}}\\ToolHub 工具箱（带日志窗口）"; Filename: "{{app}}\\AppToolHub\\runtime\\python.exe"; Parameters: "cli.py desktop"; WorkingDir: "{{app}}\\AppToolHub\\workspace\\ToolHub-工具箱门户"
Name: "{{autodesktop}}\\ToolHub 工具箱"; Filename: "{{app}}\\AppToolHub\\runtime\\pythonw.exe"; Parameters: "cli.py desktop"; WorkingDir: "{{app}}\\AppToolHub\\workspace\\ToolHub-工具箱门户"

[Run]
Filename: "{{app}}\\AppToolHub\\runtime\\pythonw.exe"; Parameters: "cli.py desktop"; WorkingDir: "{{app}}\\AppToolHub\\workspace\\ToolHub-工具箱门户"; Description: "立即启动 ToolHub 工具箱"; Flags: postinstall nowait skipifsilent
""", encoding="utf-8")
    log(f"[6] Inno 脚本：{iss}")


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    VDIR.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        # 同版本重复构建：清整个产物目录（runtime 从共享缓存重新复制）
        shutil.rmtree(OUT)
    log(f"== ToolHub 桌面版可移植包构建 v{VERSION} → {VDIR.name}/ ==")
    stage_runtime()                 # 幂等维护共享缓存 BUILD/_runtime
    log(f"[1c] 复制 runtime 到本版本产物（{human(dir_size(RUNTIME))}）…")
    shutil.copytree(RUNTIME, OUT_RUNTIME,
                    ignore=shutil.ignore_patterns("*.pyc", "__pycache__"))
    copy_workspace()
    stage_models()
    stage_launchers()
    write_inno_script()
    stage_zip()
    log(f"== 完成：{human(dir_size(OUT))} / {OUT} ==")


if __name__ == "__main__":
    main()
