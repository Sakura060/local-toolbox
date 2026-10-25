"""目录启发式：从路径判断"接收类"来源（微信/QQ/下载夹），标注为推测。"""

from __future__ import annotations

from pathlib import Path

from .config import RECEIVE_DIR_RULES


def guess_origin(path: str | Path) -> tuple[str, str] | None:
    """返回 (来源类型, 详情) 或 None。纯函数，大小写不敏感。

    用两侧带分隔符的有界匹配，避免 MyDownloads 之类子串误报。
    """
    norm = "\\" + str(path).replace("/", "\\").lower().strip("\\") + "\\"
    for label, keys in RECEIVE_DIR_RULES:
        for key in keys:
            if f"\\{key.lower()}\\" in norm:
                return label, f"路径包含 {key}（推测）"
    return None
