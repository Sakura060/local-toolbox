"""multipart/form-data 解析（Python 3.13 已移除 cgi，按 LANDrop 方案自研）。

单文件上传场景：返回 {"字段名": (文件名, 内容字节)}。
浏览器生成的 boundary 实际上不会出现在文件内容里，按边界切分安全。
"""

from __future__ import annotations

import re


def parse_multipart(body: bytes, content_type: str) -> dict[str, tuple[str, bytes]]:
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        return {}
    boundary = m.group(1).encode("latin-1")
    delim = b"--" + boundary
    out: dict[str, tuple[str, bytes]] = {}
    for part in body.split(delim)[1:]:
        part = part.lstrip(b"\r\n")
        if not part or part.startswith(b"--"):
            continue
        head_end = part.find(b"\r\n\r\n")
        if head_end < 0:
            continue
        headers = part[:head_end].decode("utf-8", "replace")
        content = part[head_end + 4:]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        name_m = re.search(r'name="([^"]*)"', headers)
        file_m = re.search(r'filename="([^"]*)"', headers)
        if name_m and file_m:
            out[name_m.group(1)] = (file_m.group(1), content)
    return out


def build_multipart(field: str, filename: str, content: bytes,
                    boundary: str = "e2eboundary123") -> tuple[bytes, str]:
    """构造 multipart 体（测试/客户端用），返回 (body, content_type)。"""
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"
