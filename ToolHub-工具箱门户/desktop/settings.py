"""桌面版服务端设置：收藏 / 排序 / 首启标记，存 data/desktop.json。

界面主题留在 localStorage（每浏览器各自记忆）；收藏与排序走服务端，
因为拖动排序要跨窗口一致，且导入导出需要完整打包。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from . import WORKSPACE_ROOT

SETTINGS_PATH = WORKSPACE_ROOT / "ToolHub-工具箱门户" / "data" / "desktop.json"
_LOCK = threading.Lock()

_DEFAULTS = {"favorites": [], "order": [], "first_run_done": False, "theme": "auto"}


def _clamp_str_list(raw, max_items=200, max_len=100) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        if isinstance(x, str) and x and len(x) <= max_len and x not in out:
            out.append(x)
        if len(out) >= max_items:
            break
    return out


def load() -> dict:
    with _LOCK:
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    merged = dict(_DEFAULTS)
    merged["favorites"] = _clamp_str_list(data.get("favorites"))
    merged["order"] = _clamp_str_list(data.get("order"))
    merged["first_run_done"] = data.get("first_run_done") is True
    merged["theme"] = data.get("theme") if data.get("theme") in ("auto", "light", "dark") else "auto"
    return merged


def save(patch: dict, valid_ids: set[str] | None = None) -> dict:
    """合并保存。valid_ids 给定时过滤掉未知工具 id（收藏/排序）。"""
    current = load()
    for key in ("favorites", "order"):
        if key in patch:
            items = _clamp_str_list(patch.get(key))
            if valid_ids is not None:
                items = [x for x in items if x in valid_ids]
            current[key] = items
    if "first_run_done" in patch:
        current["first_run_done"] = patch.get("first_run_done") is True
    if patch.get("theme") in ("auto", "light", "dark"):
        current["theme"] = patch["theme"]
    with _LOCK:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def reset() -> dict:
    with _LOCK:
        try:
            SETTINGS_PATH.unlink()
        except OSError:
            pass
    return load()
