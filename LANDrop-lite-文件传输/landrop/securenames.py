"""文件名清洗与路径安全。

上传的文件名来自外部设备，必须假定是恶意的：
  - 去掉任何路径成分（../、..\、盘符、前导斜杠）
  - 去掉控制字符与 Windows 非法字符
  - 拦截 Windows 保留设备名（CON、NUL、COM1…）
  - 限制长度，保留扩展名
"""

from __future__ import annotations

import re
import unicodedata

_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_NAME = 120


def sanitize_filename(raw: str) -> str:
    """把外部提供的文件名变成安全的纯文件名。永不抛异常。"""
    name = unicodedata.normalize("NFKC", str(raw or ""))
    # 取最后一段路径（兼容 / 与 \ 两种分隔）
    name = re.split(r"[/\\]", name)[-1]
    name = name.lstrip(".")                # 防隐藏/相对路径技巧
    name = _ILLEGAL.sub("_", name).strip().strip(".")
    if not name:
        name = "unnamed"
    stem, dot, suffix = name.rpartition(".")
    if dot and stem:
        # Windows 保留名看第一个点之前的部分（nul.tar.gz 同样是保留设备）
        first = stem.partition(".")[0]
        if first.upper() in _WIN_RESERVED:
            stem = f"{first}_{stem.partition('.')[1]}{stem.partition('.')[2]}"
        if len(stem) > _MAX_NAME:
            stem = stem[:_MAX_NAME]
    else:
        stem, suffix = name, ""
        if stem.upper() in _WIN_RESERVED:
            stem = f"{stem}_"
        if len(stem) > _MAX_NAME:
            stem = stem[:_MAX_NAME]
    out = f"{stem}.{suffix}" if suffix else stem
    return out or "unnamed"


def safe_join(base: str, name: str) -> str:
    """base + 已清洗文件名，并最终校验仍在 base 内（双保险）。"""
    import os
    full = os.path.abspath(os.path.join(base, sanitize_filename(name)))
    base_abs = os.path.abspath(base)
    if os.path.commonpath([full, base_abs]) != base_abs:
        raise ValueError(f"路径越界: {name!r}")
    return full
