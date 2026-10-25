"""LANDrop 核心 HTTP 服务。

ShareServer：把一个文件或目录共享给局域网。
  URL 结构：/s/<token>/[相对路径]   （token 即访问能力，不带 token 一律 403）
  下载支持 Range 断点续传（206/416）与 HEAD。
ReceiveServer：接收局域网设备上传。
  GET  /                     上传页面
  POST /upload               multipart 整包上传（≤2GB，小文件兼容路径）
  POST /upload-init          分块上传初始化（大文件，总量上限 10GB）
  POST /upload-chunk?id=&n=  追加一块（顺序写入磁盘临时文件，不占内存）
  POST /upload-finish        校验完整性并落盘
  POST /upload-abort         取消并清理临时文件
  GET /done                  上传成功页
TextShareServer：文本快传页面 + /raw 纯文本端点。

安全设计：
  - token 能力 URL（可 --open 关闭）
  - 上传文件名经 sanitize_filename + safe_join 双重清洗，
    落盘前再用 pathlib resolve 校验仍位于接收目录内
  - 分块上传按顺序校验块号，临时文件存放在接收目录内的专用子目录
  - 失败一律 4xx 简短说明，不泄漏服务器路径
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, parse_qs

from .multipart import parse_multipart
from .securenames import safe_join

MAX_UPLOAD = 2 * 1024 * 1024 * 1024          # 整包上传上限
MAX_CHUNKED_TOTAL = 10 * 1024 * 1024 * 1024  # 分块上传总大小上限（10GB）
MAX_CHUNK_BODY = 8 * 1024 * 1024             # 单块请求体上限
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024         # 服务端建议块大小
UPLOAD_STALE_SECONDS = 3600                  # 分块会话 1 小时无进展视为废弃
_CHUNK = 64 * 1024


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    """解析单段 Range 头（bytes=a-b / bytes=a- / bytes=-n）。

    返回 (start, end) 闭区间；多段/非法/不可满足返回 None（调用方回 416）。
    """
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", value)
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    if a == "" and b == "":
        return None
    if a == "":
        n = int(b)
        if n == 0 or size == 0:
            return None
        start, end = max(0, size - n), size - 1
    else:
        start = int(a)
        end = int(b) if b else size - 1
        if end >= size:
            end = size - 1
    if start > end or start >= size:
        return None
    return start, end


def _human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} GB"


def _resolve_under(root: Path, rel: str) -> Path:
    """把请求的相对路径安全解析到 root 内（realpath + 父目录校验）。越界抛 PermissionError。"""
    root_abs = root.resolve()
    target = (root_abs / rel).resolve()
    if target != root_abs and root_abs not in target.parents:
        raise PermissionError("路径越界")
    return target


class _Base(BaseHTTPRequestHandler):
    server_version = "LANDrop/0.1"
    protocol_version = "HTTP/1.1"
    token: str = ""
    quiet: bool = False

    # ---- 响应工具 ----
    def _json(self, obj: dict, status: int = 200, send_body: bool = True) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _html(self, body: str, status: int = 200, send_body: bool = True) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _fail(self, status: int, text: str, send_body: bool = True) -> None:
        self._html(f"<h2>❌ {html.escape(text)}</h2>", status, send_body=send_body)

    def _authorized(self, qs: dict) -> bool:
        if not self.token:
            return True
        supplied = (qs.get("pin", [""])[0] or self.headers.get("X-Landrop-Pin", ""))
        return secrets.compare_digest(supplied, self.token)

    def log_message(self, fmt, *args):
        if not self.quiet:
            super().log_message(fmt, *args)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True


class ShareHandler(_Base):
    """共享模式：GET/HEAD /s/<token>/... 下载。root 是类属性。"""
    root: Path = Path(".")
    label: str = ""

    def do_GET(self):
        self._route(send_body=True)

    def do_HEAD(self):
        self._route(send_body=False)

    def _route(self, send_body: bool):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        rel = unquote(parsed.path)
        # 剥掉 /s/<token> 前缀；路径中的 token 即访问能力
        parts = rel.split("/", 3)          # ['', 's', token, rest?]
        if len(parts) < 3 or parts[1] != "s":
            self._fail(404, "未知路径", send_body=send_body)
            return
        given = parts[2]
        if self.token and not secrets.compare_digest(given, self.token) \
                and not self._authorized(qs):
            self._fail(403, "需要访问码。请使用完整链接。", send_body=send_body)
            return
        rest = parts[3] if len(parts) > 3 else ""
        try:
            target = _resolve_under(self.root, rest) if rest else self.root.resolve()
        except PermissionError:
            self._fail(403, "路径越界", send_body=send_body)
            return

        if target.is_dir():
            self._listing(target, rest, qs, send_body)
        elif target.is_file():
            self._download(target, send_body)
        else:
            self._fail(404, "文件不存在", send_body=send_body)

    def _download(self, target: Path, send_body: bool = True):
        """下载；支持单段 Range 断点续传（206 / 416）。"""
        try:
            size = target.stat().st_size
            fh = target.open("rb")
        except OSError as e:
            self._fail(404, f"无法读取文件: {e.strerror}", send_body=send_body)
            return
        with fh:
            start, end, status = 0, size - 1, 200
            rng = self.headers.get("Range")
            if rng:
                parsed = _parse_range(rng, size)
                if parsed is None:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = parsed
                status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type",
                             mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Disposition",
                             f"attachment; filename*=UTF-8''{quote(target.name)}")
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            if not send_body:
                return
            fh.seek(start)
            remaining = length
            while remaining > 0:
                block = fh.read(min(_CHUNK, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def _listing(self, target: Path, rest: str, qs: dict, send_body: bool = True):
        prefix = f"/s/{self.token}"
        pin_q = f"?pin={quote(self.token)}" if self.token else ""
        try:
            entries = sorted(os.listdir(target), key=str.lower)
        except OSError as e:
            self._fail(404, f"无法读取目录: {e.strerror}")
            return
        rows = []
        if rest:
            parent = os.path.dirname(rest.rstrip("/"))
            back = f"{prefix}/{quote(parent)}{pin_q}" if parent else f"{prefix}{pin_q}"
            rows.append(f'<tr><td><a href="{back}">📁 ..</a></td><td></td></tr>')
        for name in entries:
            full = target / name
            rel = f"{rest.rstrip('/')}/{name}" if rest else name
            link = f"{prefix}/{quote(rel)}{pin_q}"
            if full.is_dir():
                rows.append(f'<tr><td><a href="{link}">📁 {html.escape(name)}</a></td>'
                            f"<td>目录</td></tr>")
            else:
                try:
                    size = _human(full.stat().st_size)
                except OSError:
                    size = "?"
                rows.append(f'<tr><td><a href="{link}">📄 {html.escape(name)}</a></td>'
                            f"<td>{size}</td></tr>")
        title = html.escape(self.label or target.name or "共享")
        self._html(_SHARE_PAGE.format(title=title, rows="\n".join(rows)),
                   send_body=send_body)


class ReceiveHandler(_Base):
    """接收模式。out_dir 是类属性；uploads 为全部分块会话（类级共享）。"""
    out_dir: Path = Path(".")
    uploads: dict = {}
    uploads_lock: threading.Lock = threading.Lock()
    tmp_dirname: str = ".landrop-uploads"
    browser = None  # landrop.mdns.Browser 或 None（未启用发现）

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        path = parsed.path
        if path == "/":
            if not self._authorized(qs) and not self._cookie_pin_ok():
                self._pin_page()
                return
            self._html(_RX_PAGE)
        elif path == "/done":
            self._done_page(qs)
        elif path == "/api/peers":
            # 局域网设备列表（mDNS 发现）。不泄露访问码，只给地址与模式。
            if self.browser is None or not self.browser.available:
                self._json({"available": False, "peers": []})
                return
            peers = [{**p, "open": p.get("txt", {}).get("open") == "1",
                      "mode": p.get("txt", {}).get("mode", "")}
                     for p in self.browser.peers()]
            self._json({"available": True, "peers": peers})
        else:
            self._fail(404, "未知路径")

    def do_POST(self):
        parsed = urlparse(self.path)
        route = {
            "/upload": self._post_upload,
            "/upload-init": self._post_upload_init,
            "/upload-chunk": self._post_upload_chunk,
            "/upload-finish": self._post_upload_finish,
            "/upload-abort": self._post_upload_abort,
        }.get(parsed.path)
        if route is None:
            self._fail(404, "未知路径")
            return
        qs = parse_qs(parsed.query)
        if not self._authorized(qs) and not self._cookie_pin_ok():
            self._fail(403, "需要访问码")
            return
        route(qs)

    # ---- 请求体工具 ----
    def _read_body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > limit:
            raise ValueError(f"请求体大小不合法（上限 {_human(limit)}）")
        data = bytearray()
        remaining = length
        while remaining > 0:
            block = self.rfile.read(min(256 * 1024, remaining))
            if not block:
                break
            data.extend(block)
            remaining -= len(block)
        if remaining:
            raise ValueError("请求体不完整")
        return bytes(data)

    def _read_json(self, limit: int = 64 * 1024) -> dict:
        raw = self._read_body(limit)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"JSON 不合法: {e}")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    # ---- 分块上传会话管理 ----
    def _tmp_dir(self) -> Path:
        d = self.out_dir / self.tmp_dirname
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _sweep_stale(self) -> None:
        """清理超时未完成的分块会话。须持有 uploads_lock。"""
        now = time.time()
        stale = [uid for uid, s in self.uploads.items()
                 if now - s["last_ts"] > UPLOAD_STALE_SECONDS]
        for uid in stale:
            self._discard_locked(uid)

    def _discard_locked(self, uid: str) -> None:
        s = self.uploads.pop(uid, None)
        if s:
            try:
                Path(s["path"]).unlink(missing_ok=True)
            except OSError:
                pass

    def _post_upload_init(self, qs):
        try:
            data = self._read_json()
        except ValueError as e:
            self._fail(400, str(e))
            return
        try:
            size = int(data.get("size", -1))
        except (TypeError, ValueError):
            size = -1
        filename = str(data.get("filename", ""))
        if not filename or size < 0:
            self._fail(400, "需要 filename 与 size")
            return
        if size > MAX_CHUNKED_TOTAL:
            self._fail(400, f"文件超过分块上传上限（{_human(MAX_CHUNKED_TOTAL)}）")
            return
        safe_name = safe_join(str(self.out_dir.resolve()), filename)
        final = Path(safe_name).resolve()
        out_abs = self.out_dir.resolve()
        if final != out_abs and out_abs not in final.parents:
            self._fail(400, "文件名不合法")
            return

        with self.uploads_lock:
            self._sweep_stale()
            uid = secrets.token_urlsafe(12)
            while uid in self.uploads:
                uid = secrets.token_urlsafe(12)
            tmp = self._tmp_dir() / f"{uid}.part"
            tmp.touch()
            self.uploads[uid] = {
                "name": final.name,            # 已清洗的最终文件名
                "size": size,
                "chunk_size": DEFAULT_CHUNK_SIZE,
                "path": str(tmp),
                "received": 0,
                "last_ts": time.time(),
            }
            s = self.uploads[uid]
        self._json({"id": uid, "chunk_size": s["chunk_size"],
                    "total_chunks": -(-size // s["chunk_size"])})

    def _post_upload_chunk(self, qs):
        uid = qs.get("id", [""])[0]
        try:
            n = int(qs.get("n", [""])[0])
        except ValueError:
            n = -1
        with self.uploads_lock:
            s = self.uploads.get(uid)
            if s is None:
                self._fail(404, "上传会话不存在或已过期")
                return
            if n != s["received"]:
                # 严格要求顺序追加，避免乱序覆盖
                self._fail(409, f"块号不连续：期望 {s['received']}，收到 {n}")
                return
        try:
            body = self._read_body(MAX_CHUNK_BODY)
        except ValueError as e:
            self._fail(400, str(e))
            return
        with self.uploads_lock:
            s = self.uploads.get(uid)
            if s is None:
                self._fail(404, "上传会话不存在或已过期")
                return
            try:
                with open(s["path"], "ab") as fp:
                    fp.write(body)
            except OSError as e:
                self._fail(500, f"写入失败: {e.strerror}")
                return
            s["received"] += 1
            s["last_ts"] = time.time()
            received, total = s["received"], s["size"]
        self._json({"received": received,
                    "total_chunks": -(-total // s["chunk_size"])})

    def _post_upload_finish(self, qs):
        uid = str(self._safe_json(qs).get("id", ""))
        with self.uploads_lock:
            s = self.uploads.get(uid)
            if s is None:
                self._fail(404, "上传会话不存在或已过期")
                return
            got = os.path.getsize(s["path"]) if os.path.exists(s["path"]) else -1
            if got != s["size"]:
                self._fail(409, f"分块不完整（{got}/{s['size']} 字节）")
                return
            dest = Path(safe_join(str(self.out_dir.resolve()), s["name"]))
            final = dest.resolve()
            out_abs = self.out_dir.resolve()
            if final != out_abs and out_abs not in final.parents:
                self._fail(400, "路径越界")
                return
            try:
                # 重名自动追加 (1)(2)，不覆盖已有文件
                candidate = final
                i = 1
                while candidate.exists():
                    stem, dot, suffix = final.name.rpartition(".")
                    if not dot or stem == "":
                        stem, suffix = final.name, ""
                    candidate = final.with_name(f"{stem} ({i}).{suffix}" if suffix
                                                else f"{stem} ({i})")
                    i += 1
                os.replace(s["path"], candidate)
            except OSError as e:
                self._fail(500, f"落盘失败: {e.strerror}")
                return
            self.uploads.pop(uid, None)
        self._json({"saved": candidate.name, "size": s["size"]})

    def _post_upload_abort(self, qs):
        uid = str(self._safe_json(qs).get("id", ""))
        with self.uploads_lock:
            if uid not in self.uploads:
                self._fail(404, "上传会话不存在或已过期")
                return
            self._discard_locked(uid)
        self._json({"aborted": uid})

    def _safe_json(self, qs) -> dict:
        try:
            return self._read_json()
        except ValueError:
            return {}

    # ---- 旧整包上传（小文件 / curl 兼容） ----
    def _post_upload(self, qs):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._fail(400, "请通过上传页面提交")
            return
        try:
            body = self._read_body(MAX_UPLOAD)
        except ValueError as e:
            self._fail(400, str(e))
            return
        try:
            result = parse_multipart(body, ctype)
        except ValueError as e:
            self._fail(400, f"上传数据不合法: {e}")
            return
        if not result.files:
            self._fail(400, "没有收到文件")
            return
        saved, errors = [], []
        out_abs = self.out_dir.resolve()
        for f in result.files:
            if not f.filename:
                continue
            try:
                dest = Path(safe_join(str(out_abs), f.filename))
                # 双保险：最终落盘前再校验一次仍位于接收目录内
                final = dest.resolve()
                if final != out_abs and out_abs not in final.parents:
                    raise PermissionError("路径越界")
                with final.open("wb") as out:
                    out.write(f.data)
                saved.append((final.name, len(f.data)))
            except (OSError, ValueError, PermissionError) as e:
                errors.append(f"{f.filename}: {e}")
        if saved:
            q = "&".join(f"n={quote(n)}&s={s}" for n, s in saved[:10])
            if errors:
                q += "&err=" + quote("; ".join(errors[:5]))
            self._redirect(f"/done?{q}")
        else:
            self._fail(400, "全部文件保存失败: " + "; ".join(errors[:3]))

    # ---- 访问码 Cookie ----
    def _cookie_pin_ok(self) -> bool:
        if not self.token:
            return True
        cookies = self.headers.get("Cookie", "")
        for part in cookies.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "landrop_pin" and v and secrets.compare_digest(v, self.token):
                return True
        return False

    def _pin_page(self):
        self._html(_PIN_PAGE, 403)

    def _done_page(self, qs):
        names = [unquote(v) for v in qs.get("n", [])]
        sizes = {unquote(k): v for k, v in zip(qs.get("n", []), qs.get("s", []))}
        errs = unquote(qs.get("err", [""])[0])
        items = "".join(
            f'<li>📄 {html.escape(n)} <span class="muted">({_human(int(sizes.get(n, 0)))})</span></li>'
            for n in names)
        err_html = f'<p class="err">部分失败: {html.escape(errs)}</p>' if errs else ""
        self._html(_DONE_PAGE.format(items=items or "<li>无</li>", err=err_html))


class TextShareHandler(_Base):
    """文本快传：/s/<token> 显示页面，/s/<token>/raw 返回纯文本。"""
    text: str = ""
    label: str = "文本快传"

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        parts = unquote(parsed.path).split("/", 3)   # ['', 's', token, rest?]
        if len(parts) < 3 or parts[1] != "s":
            self._fail(404, "未知路径")
            return
        given = parts[2]
        if self.token and not secrets.compare_digest(given, self.token) \
                and not self._authorized(qs):
            self._fail(403, "需要访问码。请使用完整链接。")
            return
        rest = parts[3] if len(parts) > 3 else ""
        if rest in ("", "/", "raw"):
            body = self.text.replace("\r\n", "\n")
            if rest == "raw":
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._html(_TEXT_PAGE.format(
                    full=html.escape(body),
                    size=_human(len(body.encode("utf-8"))),
                ))
        else:
            self._fail(404, "未知路径")


# ---------------- 页面模板 ----------------

_SHARE_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · LANDrop</title><style>
body{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:#f2f6f9;margin:0}}
header{{background:#0b7285;color:#fff;padding:16px 24px}}h1{{margin:0;font-size:19px}}
main{{max-width:760px;margin:20px auto;padding:0 14px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;
overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
td{{padding:10px 14px;border-bottom:1px solid #e8eef3;font-size:14px;word-break:break-all}}
td:last-child{{color:#789;white-space:nowrap;text-align:right}}
a{{color:#0b7285;text-decoration:none}}a:hover{{text-decoration:underline}}
</style></head><body>
<header><h1>📤 {title}</h1><p style="margin:4px 0 0;opacity:.8;font-size:13px">
LANDrop 局域网共享 · 点击即可下载</p></header>
<main><table>{rows}</table>
<p style="text-align:center;color:#9ab;font-size:12px">LANDrop-lite · 仅限局域网访问</p>
</main></body></html>"""

_RX_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>上传到电脑 · LANDrop</title><style>
body{font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:#f2f6f9;margin:0}
header{background:#5f3dc4;color:#fff;padding:16px 24px}h1{margin:0;font-size:19px}
main{max-width:640px;margin:22px auto;padding:0 14px}
.card{background:#fff;border-radius:12px;padding:26px;box-shadow:0 1px 5px rgba(0,0,0,.08)}
#drop{border:2px dashed #b6bce0;border-radius:12px;padding:36px 16px;text-align:center;
color:#6673a8;font-size:15px;transition:.15s}
#drop.over{background:#eef0ff;border-color:#5f3dc4}
input[type=file]{display:none}
button{margin-top:16px;width:100%;padding:13px;border:0;border-radius:10px;
background:#5f3dc4;color:#fff;font-size:16px;cursor:pointer}
button:disabled{opacity:.5}
#bar{height:8px;background:#e3e6f5;border-radius:4px;margin-top:14px;overflow:hidden;display:none}
#fill{height:100%;width:0;background:#5f3dc4;transition:width .2s}
#st{text-align:center;color:#687;font-size:13px;margin-top:10px;min-height:18px}
</style></head><body>
<header><h1>📥 上传文件到电脑</h1>
<p style="margin:4px 0 0;opacity:.8;font-size:13px">手机/平板打开此页面即可传文件 · 文件保存在电脑的接收目录</p></header>
<main><div class="card">
<form id="f">
<div id="drop">点击选择 或 把文件拖到这里<br><span style="font-size:12px">支持多选</span></div>
<input id="files" type="file" multiple name="files">
<button id="go" type="submit">开始上传</button>
<div id="bar"><div id="fill"></div></div><div id="st"></div>
</form></div>
<div class="card" id="peersCard" style="margin-top:14px;display:none">
<div style="font-weight:600;font-size:14px;margin-bottom:8px">📡 局域网里的 LANDrop 设备</div>
<div id="peers" style="font-size:13px;color:#556">正在发现…</div>
</div></main>
<script>
"use strict";
const drop=document.getElementById("drop"),fi=document.getElementById("files"),
go=document.getElementById("go"),bar=document.getElementById("bar"),
fill=document.getElementById("fill"),st=document.getElementById("st");
/* ---- 设备发现（mDNS） ---- */
async function refreshPeers(){
  try{
    const r=await fetch("/api/peers"); const j=await r.json();
    if(!j.available){return}
    const box=document.getElementById("peers");
    document.getElementById("peersCard").style.display="block";
    if(!j.peers.length){box.textContent="暂未发现其他设备（双方都开着 LANDrop 才能看到）";return}
    box.replaceChildren();
    for(const p of j.peers){
      const d=document.createElement("div"); d.style.margin="4px 0";
      const a=document.createElement("a");
      a.href="http://"+p.ip+":"+p.port+"/";
      a.target="_blank"; a.rel="noopener";
      a.textContent=(p.instance||"未知设备")+" ("+p.ip+":"+p.port+")"+(p.mode?" · "+p.mode:"");
      const note=document.createElement("span");
      note.style.color="#889"; note.style.fontSize="12px";
      note.textContent=p.open?"  开放访问":"  需访问码（打开后输入）";
      d.append(a,note); box.append(d);
    }
  }catch(e){/* 发现是增强功能，失败静默 */}
}
refreshPeers(); setInterval(refreshPeers,4000);
drop.onclick=()=>fi.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add("over")};
drop.ondragleave=()=>drop.classList.remove("over");
drop.ondrop=e=>{e.preventDefault();drop.classList.remove("over");
fi.files=e.dataTransfer.files};
document.getElementById("f").onsubmit=async (e)=>{
e.preventDefault();
if(!fi.files.length){st.textContent="请先选择文件";return}
go.disabled=true;bar.style.display="block";fill.style.width="0%";
const files=[...fi.files];
const saved=[];
try{
for(let i=0;i<files.length;i++){
const file=files[i];
st.textContent="上传中 ("+(i+1)+"/"+files.length+"): "+file.name;
const initR=await fetch("/upload-init",{method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({filename:file.name,size:file.size})});
if(!initR.ok){throw new Error("初始化: HTTP "+initR.status)}
const info=await initR.json();
const total=info.total_chunks;
for(let n=0;n<total;n++){
const blob=file.slice(n*info.chunk_size,(n+1)*info.chunk_size);
const r=await fetch("/upload-chunk?id="+encodeURIComponent(info.id)+"&n="+n,{method:"POST",body:blob});
if(!r.ok){throw new Error("分块上传: HTTP "+r.status)}
const overall=(i+(n+1)/total)/files.length*100;
fill.style.width=overall.toFixed(1)+"%";
}
const finR=await fetch("/upload-finish",{method:"POST",
headers:{"Content-Type":"application/json"},body:JSON.stringify({id:info.id})});
if(!finR.ok){throw new Error("完成上传: HTTP "+finR.status)}
const fin=await finR.json();
saved.push({name:fin.saved,size:fin.size});
}
const q=saved.map(s=>"n="+encodeURIComponent(s.name)+"&s="+s.size).join("&");
location="/done?"+q;
}catch(err){
st.textContent="上传失败: "+err.message;go.disabled=false;bar.style.display="none";
}
};
</script></body></html>"""

_DONE_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>上传完成 · LANDrop</title><style>
body{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:#f2f6f9;margin:0;
display:flex;min-height:100vh;align-items:center;justify-content:center}}
.card{{background:#fff;border-radius:12px;padding:30px 34px;
box-shadow:0 1px 5px rgba(0,0,0,.08);max-width:520px}}
h2{{margin:0 0 12px;color:#2b8a3e}}li{{font-size:14px;margin:6px 0;word-break:break-all}}
.muted{{color:#889}}a{{color:#5f3dc4}}.err{{color:#c92a2a;font-size:13px}}
</style></head><body><div class="card">
<h2>✅ 上传完成</h2><ul>{items}</ul>{err}
<a href="/">继续上传</a></div></body></html>"""

_PIN_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>访问码 · LANDrop</title><style>
body{font-family:sans-serif;background:#f2f6f9;margin:0;display:flex;
min-height:100vh;align-items:center;justify-content:center}
.card{background:#fff;border-radius:12px;padding:30px;box-shadow:0 1px 5px rgba(0,0,0,.08)}
input{padding:10px;font-size:16px;border:1px solid #ccd;border-radius:8px;width:180px}
button{padding:10px 18px;border:0;border-radius:8px;background:#5f3dc4;color:#fff}
</style></head><body><div class="card">
<h2>🔒 请输入访问码</h2>
<form method="get" action="/">
<input name="pin" autofocus placeholder="访问码">
<button>进入</button></form></div></body></html>"""

_TEXT_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>文本快传 · LANDrop</title><style>
body{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:#f2f6f9;margin:0}}
header{{background:#1971c2;color:#fff;padding:16px 24px}}h1{{margin:0;font-size:19px}}
main{{max-width:640px;margin:20px auto;padding:0 14px}}
.card{{background:#fff;border-radius:12px;padding:22px;box-shadow:0 1px 5px rgba(0,0,0,.08)}}
pre{{white-space:pre-wrap;word-break:break-all;font-size:15px;line-height:1.55;
background:#f8f9fb;border-radius:8px;padding:14px;max-height:60vh;overflow:auto;margin:0}}
button{{margin-top:14px;width:100%;padding:12px;border:0;border-radius:10px;
background:#1971c2;color:#fff;font-size:15px;cursor:pointer}}
.muted{{color:#8a93a2;font-size:12px;text-align:center;margin-top:10px}}
</style></head><body>
<header><h1>📋 文本快传</h1>
<p style="margin:4px 0 0;opacity:.8;font-size:13px">来自电脑的一段文本 · {size}</p></header>
<main><div class="card"><pre id="t">{full}</pre>
<button onclick="cp()">复制全文</button>
<p class="muted">若复制失败，请长按文本手动选择复制</p></div></main>
<script>
function cp(){{
const t=document.getElementById("t").textContent;
if(navigator.clipboard&&navigator.clipboard.writeText){{
navigator.clipboard.writeText(t).then(()=>{{
document.querySelector("button").textContent="✅ 已复制";
setTimeout(()=>document.querySelector("button").textContent="复制全文",1500);
}}).catch(()=>{{alert("复制失败，请长按文本手动复制")}});
}}else{{alert("此浏览器不支持自动复制，请长按文本手动复制")}}
}}
</script></body></html>"""


def make_share_server(root: str, token: str = "", host: str = "0.0.0.0",
                      port: int = 8800, quiet: bool = False) -> ThreadingHTTPServer:
    handler = type("H", (ShareHandler,), {"root": Path(root).absolute(),
                                          "label": os.path.basename(root) or root,
                                          "token": token, "quiet": quiet})
    return ThreadingHTTPServer((host, port), handler)


def make_receive_server(out_dir: str, token: str = "", host: str = "0.0.0.0",
                        port: int = 8801, quiet: bool = False,
                        browser=None) -> ThreadingHTTPServer:
    os.makedirs(out_dir, exist_ok=True)
    handler = type("H", (ReceiveHandler,), {"out_dir": Path(out_dir).absolute(),
                                            "token": token, "quiet": quiet,
                                            "browser": browser})
    return ThreadingHTTPServer((host, port), handler)


def make_text_server(text: str, token: str = "", host: str = "0.0.0.0",
                     port: int = 8802, quiet: bool = False) -> ThreadingHTTPServer:
    handler = type("H", (TextShareHandler,), {"text": text,
                                              "token": token, "quiet": quiet})
    return ThreadingHTTPServer((host, port), handler)
