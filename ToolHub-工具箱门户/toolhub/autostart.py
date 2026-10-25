"""开机自启（ToolHub 专用壳）：通用件在 localcore.autostart（D27 上收）。

往当前用户的 Startup 文件夹放一个 VBS（隐藏窗口启动托盘）。
"""

from __future__ import annotations

from pathlib import Path

from localcore.autostart import (  # noqa: F401  pythonw_path/vbs_content 供旧测试复用
    pythonw_path,
    startup_dir,
    vbs_content,
)
from localcore.autostart import install as _install
from localcore.autostart import is_installed as _is_installed
from localcore.autostart import uninstall as _uninstall

VBS_NAME = "toolhub_tray.vbs"


def install(root: Path, target_dir: Path | None = None) -> Path:
    """写入自启动 VBS，返回 VBS 路径。root = ToolHub 项目目录。"""
    return _install(root, VBS_NAME, "tray", target_dir)


def uninstall(target_dir: Path | None = None) -> bool:
    return _uninstall(VBS_NAME, target_dir)


def is_installed(target_dir: Path | None = None) -> bool:
    return _is_installed(VBS_NAME, target_dir)
