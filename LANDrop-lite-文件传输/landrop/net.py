"""网络工具：局域网 IP 探测、URL 组装、终端二维码（可选依赖）。"""

from __future__ import annotations

import socket


def lan_ip() -> str:
    """探测本机在局域网中的 IP（UDP connect 不发任何包）。失败回退 127.0.0.1。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def print_qrcode(text: str) -> bool:
    """终端打印二维码；未安装 qrcode 包时返回 False（优雅降级）。"""
    try:
        import qrcode
    except ImportError:
        return False
    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    return True
