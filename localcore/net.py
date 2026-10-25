"""localcore.net：本地工具间 HTTP 访问的标准件。

从 ToolHub v0.7 的 proxy.py 抽取（2026-09-27），任何需要"访问本机其他
工具端口"的项目都应复用这里，而不是各抄一份。

安全约束：
- 主机一律是字面量 127.0.0.1（各工具只绑回环；不用动态拼 URL）；
- 只允许 GET；响应体有大小上限（防止把大文件读进内存）；
- path_allowed 是段边界精确的前缀匹配（/api 允许 /api 与 /api/x，拒绝 /apix），
  供反向代理做透传白名单。
"""

from __future__ import annotations

import http.client
import time
from dataclasses import dataclass

HOST = "127.0.0.1"

DEFAULT_TIMEOUT = 2.5
PROBE_TIMEOUT = 0.8
MAX_BODY = 8 * 1024 * 1024  # 8MB，透传响应上限


@dataclass(frozen=True)
class FetchResult:
    status: int
    content_type: str
    body: bytes
    truncated: bool = False


class UpstreamError(Exception):
    """上游不可达/超时/协议错误，与 HTTP 状态码错误区分开。"""


def fetch(port: int, path_qs: str, timeout: float = DEFAULT_TIMEOUT,
          max_bytes: int = MAX_BODY) -> FetchResult:
    """GET 上游并读取响应体（最多 max_bytes，多读 1 字节判截断）。"""
    if not path_qs.startswith("/"):
        raise UpstreamError("path must start with /")
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        conn.request("GET", path_qs, headers={"Accept": "*/*"})
        resp = conn.getresponse()
        body = resp.read(max_bytes + 1)
        ctype = resp.getheader("Content-Type") or "application/octet-stream"
        return FetchResult(
            status=resp.status,
            content_type=ctype,
            body=body[:max_bytes],
            truncated=len(body) > max_bytes,
        )
    except (OSError, http.client.HTTPException) as exc:
        raise UpstreamError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        conn.close()


def probe(port: int, path: str = "/", timeout: float = PROBE_TIMEOUT) -> tuple[bool, int]:
    """探测工具是否在线。返回 (online, 耗时ms)；任何 HTTP 应答都算在线。"""
    start = time.perf_counter()
    try:
        conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    except OSError:
        return False, 0
    try:
        # Connection: close：只读 64 字节就断开，避免上游收到 RST 噪音
        conn.request("GET", path, headers={"Connection": "close", "Accept": "*/*"})
        resp = conn.getresponse()
        resp.read(64)
        elapsed = int((time.perf_counter() - start) * 1000)
        return True, elapsed
    except (OSError, http.client.HTTPException):
        return False, 0
    finally:
        conn.close()


def path_allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    """段边界精确的路径前缀匹配：/api 允许 /api 与 /api/x，拒绝 /apix。"""
    if not path.startswith("/"):
        return False
    for p in prefixes:
        if path == p or path.startswith(p + "/"):
            return True
    return False
