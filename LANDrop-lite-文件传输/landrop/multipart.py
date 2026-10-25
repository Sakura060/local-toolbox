"""multipart/form-data 解析器（cgi 模块已在 Python 3.13 移除，故自带）。

只支持单请求多字段 + 文件字段的标准浏览器编码。流式读取由调用方负责，
这里在内存中解析（LANDrop 的单文件上限默认 2GB 以下场景足够，
实现上按 boundary 切块而非整包读入）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Part:
    name: str
    filename: str | None = None      # 文件字段才有
    content_type: str = ""
    data: bytes = b""


@dataclass
class MultipartResult:
    fields: dict[str, str] = field(default_factory=dict)
    files: list[Part] = field(default_factory=list)


def parse_multipart(body: bytes, content_type: str) -> MultipartResult:
    """解析 multipart/form-data 请求体。非法输入抛 ValueError。"""
    if "boundary=" not in content_type:
        raise ValueError("Content-Type 缺少 boundary")
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
    if not boundary:
        raise ValueError("boundary 为空")
    delim = b"--" + boundary
    result = MultipartResult()

    # 按分隔符切块：首块为 preamble（通常空），末块为 "--"
    chunks = body.split(delim)
    if len(chunks) < 2:
        raise ValueError("请求体中没有 multipart 分块")
    for chunk in chunks[1:-1]:
        # 每块形如 \r\nheaders\r\n\r\ndata\r\n
        if chunk in (b"--", b"--\r\n"):
            continue
        chunk = chunk.lstrip(b"\r\n")
        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            raise ValueError("分块缺少 header/body 分隔")
        data = data.rstrip(b"\r\n")     # 尾部 CRLF 属于边界
        headers = _parse_headers(head.decode("utf-8", "replace"))
        disp = headers.get("content-disposition", "")
        name = _disp_param(disp, "name")
        filename = _disp_param(disp, "filename")
        if not name:
            continue
        if filename is not None:
            result.files.append(Part(name=name, filename=filename,
                                     content_type=headers.get("content-type", ""),
                                     data=data))
        else:
            result.fields[name] = data.decode("utf-8", "replace")
    return result


def _parse_headers(head: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in head.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return headers


def _disp_param(disposition: str, key: str) -> str | None:
    """从 Content-Disposition 取参数；filename 参数不存在返回 None（区别于空串）。"""
    import re
    m = re.search(rf'{key}\s*=\s*"((?:[^"\\]|\\.)*)"|{key}\s*=\s*([^;\s]+)', disposition)
    if not m:
        return None
    val = m.group(1) if m.group(1) is not None else m.group(2)
    return val.replace('\\"', '"')
