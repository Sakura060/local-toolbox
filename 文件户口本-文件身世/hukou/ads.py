"""Zone.Identifier ADS 读取：浏览器/聊天软件下载的文件自带"来源证书"。

Zone.Identifier 是 NTFS 备用数据流，内容形如：
    [ZoneTransfer]
    ZoneId=3
    ReferrerUrl=https://example.com/page
    HostUrl=https://example.com/file.exe

读不到返回 None（本机生成/复制的文件没有该流）。
"""

from __future__ import annotations

from pathlib import Path


def parse_zone_text(text: str) -> dict[str, str]:
    """解析 Zone.Identifier 文本为字典（纯函数）。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def read_zone_identifier(path: str | Path) -> dict[str, str] | None:
    """读取文件的 Zone.Identifier。失败/不存在返回 None，绝不抛异常。"""
    p = Path(path)
    if not p.is_file():
        return None
    ads_path = f"{p}:Zone.Identifier"
    try:
        with __import__("builtins").open(
            ads_path, "r", encoding="utf-8", errors="replace"
        ) as f:
            text = f.read(64 * 1024)
    except OSError:
        return None
    data = parse_zone_text(text)
    return data or None


def origin_from_zone(zone: dict[str, str]) -> tuple[str, str]:
    """从 Zone 字典提取 (来源类型, 详情)。"""
    host = zone.get("HostUrl", "")
    ref = zone.get("ReferrerUrl", "")
    if host and host not in ("about:internet", "Local Machine"):
        detail = host
        if ref:
            detail += f"（来自页面 {ref}）"
        return "网络下载", detail
    if zone.get("LastWriterPackageFamilyName"):
        return "应用保存", zone["LastWriterPackageFamilyName"]
    return "外部来源", "; ".join(f"{k}={v}" for k, v in zone.items() if k != "ZoneId")
