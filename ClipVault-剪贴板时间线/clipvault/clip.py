"""剪贴板采集与回贴（Windows）。

读取用 Pillow 的 ImageGrab.grabclipboard()（文本/文件列表/图片一站式）；
序列号用 user32.GetClipboardSequenceNumber（免开剪贴板）；
回贴支持三种格式：CF_UNICODETEXT 文本 / CF_DIB 位图 / CF_HDROP 文件列表。

测试绝不碰真实剪贴板——Poller 的分发逻辑拆成纯函数 testable_dispatch，
回贴的字节构造（png_to_dib / build_hdrop）是纯函数，ctypes 层可在测试里换假件。
"""

from __future__ import annotations

import ctypes
import struct
import threading
import time
from pathlib import Path

from .store import Store, content_hash

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT = 13
CF_DIB = 8
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002


def sequence_number() -> int:
    return int(user32.GetClipboardSequenceNumber())


def read_snapshot():
    """返回 (kind, body, image_bytes) 三元组；空剪贴板/不支持 → (None, '', b'')。

    kind: text / files / image
    """
    from PIL import ImageGrab

    data = ImageGrab.grabclipboard()
    if data is None:
        return None, "", b""
    if isinstance(data, str):
        if not data.strip():
            return None, "", b""
        return "text", data, b""
    if isinstance(data, list):
        paths = "\n".join(str(p) for p in data)
        if not paths:
            return None, "", b""
        return "files", paths, b""
    # PIL.Image.Image
    from io import BytesIO

    buf = BytesIO()
    data.save(buf, "PNG")
    return "image", "", buf.getvalue()


def _set_clipboard_data(cf_format: int, data: bytes) -> bool:
    """把一段字节作为指定格式放入剪贴板（先 EmptyClipboard）。

    打不开/分配失败/设置失败都返回 False，不抛异常。
    """
    if not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(cf_format, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


def write_text(text: str) -> bool:
    """把文本放回剪贴板（CF_UNICODETEXT，UTF-16-LE 双 NUL 结尾）。"""
    return _set_clipboard_data(CF_UNICODETEXT,
                               text.encode("utf-16-le") + b"\x00\x00")


def png_to_dib(png_bytes: bytes) -> bytes:
    """PNG → CF_DIB（BITMAPINFOHEADER + 底朝上 BGRA 像素）。

    32bpp BI_RGB：透明度保留在第 4 字节（现代应用按 alpha 处理）；
    行按 BMP 惯例自下而上，32bpp 天然 4 字节对齐无需补位。
    """
    from io import BytesIO

    from PIL import Image
    with Image.open(BytesIO(png_bytes)) as im:
        rgba = im.convert("RGBA")
        w, h = rgba.size
        pixels = rgba.load()
        rows = bytearray()
        for y in range(h - 1, -1, -1):
            for x in range(w):
                r, g, b, a = pixels[x, y]
                rows += struct.pack("BBBB", b, g, r, a)
    header = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0,
                         w * h * 4, 0, 0, 0, 0)
    return header + bytes(rows)


def write_image(png_bytes: bytes) -> bool:
    """把图片放回剪贴板（CF_DIB 位图，粘贴目标可直接得到图像本身）。"""
    return _set_clipboard_data(CF_DIB, png_to_dib(png_bytes))


def build_hdrop(paths: list[str]) -> bytes:
    """路径列表 → CF_HDROP 字节（DROPFILES 头 + UTF-16-LE 双 NUL 结尾串）。"""
    part = "\x00".join(paths) + "\x00\x00"
    # pFiles=20, pt=(0,0), fNC=0, fWide=1 —— 全部 4 字节字段，共 20 字节头
    header = struct.pack("<IIiiI", 20, 0, 0, 0, 1)
    return header + part.encode("utf-16-le")


def write_files(paths: list[str]) -> bool:
    """把文件列表放回剪贴板（CF_HDROP，粘贴目标按文件处理）。

    空列表/全空行返回 False（HDROP 至少要有一个文件）；
    保留原始路径字符串，只过滤纯空行。
    """
    clean = [p for p in paths if p.strip()]
    if not clean:
        return False
    return _set_clipboard_data(CF_HDROP, build_hdrop(clean))


def dhash_png(png_bytes: bytes) -> int:
    """图片感知哈希（9×8 灰度 dHash，纯 Pillow 无 freetype，同捞图 D21 方案）。

    64 位：相邻像素比较（左 < 右）置 1。用于"回贴出去的图被重新采集"的
    感知去重——DIB→PNG 重编码字节不同，结构相同。
    """
    from io import BytesIO

    from PIL import Image
    with Image.open(BytesIO(png_bytes)) as im:
        g = im.convert("L").resize((9, 8), Image.LANCZOS)
        px = list(g.getdata())
    bits = 0
    for y in range(8):
        row = px[y * 9:(y + 1) * 9]
        for x in range(8):
            if row[x] < row[x + 1]:
                bits |= 1 << (y * 8 + x)
    return bits


def dispatch(store: Store, snapshot, images_dir: Path) -> dict | None:
    """把 read_snapshot 的结果写入 store（图片落盘）。纯逻辑，测试友好。"""
    kind, body, image_bytes = snapshot
    if kind is None:
        return None
    if kind == "image":
        chash = content_hash(image_bytes)
        name = f"{int(time.time() * 1000)}_{chash[:8]}.png"
        (images_dir / name).write_bytes(image_bytes)
        entry = store.capture("image", "", image_path=name,
                              size=len(image_bytes), chash=chash,
                              dhash=dhash_png(image_bytes))
        if entry is not None and entry["image_path"] != name:
            # 命中去重上浮了旧条目：刚落盘的这份是孤儿，清掉
            try:
                (images_dir / name).unlink()
            except OSError:
                pass
        return entry
    if not body.strip():
        return None
    # 超大文本入库截断（hash 按原始内容算，去重不受影响）
    if len(body) > 500_000:
        body = body[:500_000] + "\n…[已截断，原长度 {} 字符]".format(
            len(body))
    return store.capture(kind, body, size=len(body.encode("utf-8")),
                         chash=content_hash(body))


class Poller(threading.Thread):
    """轮询剪贴板序列号，变化即采集。daemon 线程。"""

    def __init__(self, store: Store, images_dir: Path, interval: float = 1.5):
        super().__init__(daemon=True, name="clipvault-poller")
        self.store = store
        self.images_dir = Path(images_dir)
        self.interval = max(0.5, float(interval))
        self.stop_event = threading.Event()
        self.last_seq = sequence_number()
        self.captured = 0

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                seq = sequence_number()
                if seq == self.last_seq:
                    continue
                self.last_seq = seq
                if self.store.stats()["paused"]:
                    continue
                entry = dispatch(self.store, read_snapshot(), self.images_dir)
                if entry is not None:
                    self.captured += 1
            except Exception as exc:  # 剪贴板被别的进程占用等瞬态错误，忽略本轮
                try:
                    self._log_err(exc)
                except Exception:
                    pass

    def _log_err(self, exc: Exception) -> None:
        with (self.images_dir.parent / "poll_err.log").open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {type(exc).__name__}: {exc}\n")

    def stop(self) -> None:
        self.stop_event.set()
