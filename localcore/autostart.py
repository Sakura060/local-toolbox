"""开机自启通用件：往当前用户 Startup 文件夹放一个 VBS（隐藏窗口启动 pythonw 脚本）。

VBS 用 ANSI（mbcs）编码写——WScript 按系统代码页解析，UTF-8 中文路径会乱码。
target_dir 参数供测试注入临时目录；真实路径用 %APPDATA% 的 Startup。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def pythonw_path() -> str:
    """python.exe -> pythonw.exe（同目录；虚拟环境同样适用）。"""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def vbs_content(pythonw: str, target_py: Path, args: str = "") -> str:
    """生成 VBS 内容。引号包路径（含空格/中文都安全）；0=隐藏窗口，False=不等待。"""
    cmd = f'"""{pythonw}"" ""{target_py}"""'
    if args:
        cmd += f" {args}"
    return f'CreateObject("WScript.Shell").Run {cmd}, 0, False'


def startup_dir() -> Path:
    base = os.environ.get("APPDATA")
    if not base:
        raise RuntimeError("APPDATA 环境变量不存在")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def install(root: Path, vbs_name: str, args: str = "",
            target_dir: Path | None = None) -> Path:
    """写入自启动 VBS，返回 VBS 路径。root = 项目目录（必须含 cli.py）。"""
    directory = target_dir if target_dir is not None else startup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target_py = root / "cli.py"
    if not target_py.is_file():
        raise FileNotFoundError(f"cli.py 不存在: {target_py}")
    content = vbs_content(pythonw_path(), target_py, args)
    vbs = directory / vbs_name
    vbs.write_bytes(content.encode("mbcs", errors="replace"))
    return vbs


def uninstall(vbs_name: str, target_dir: Path | None = None) -> bool:
    directory = target_dir if target_dir is not None else startup_dir()
    vbs = directory / vbs_name
    if vbs.is_file():
        vbs.unlink()
        return True
    return False


def is_installed(vbs_name: str, target_dir: Path | None = None) -> bool:
    directory = target_dir if target_dir is not None else startup_dir()
    return (directory / vbs_name).is_file()
