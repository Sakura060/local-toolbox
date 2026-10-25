"""LANDrop 命令行入口。

用法:
  python cli.py share <文件或目录> [--port 8800] [--open] [--loopback]
  python cli.py receive [--out 接收目录] [--port 8801] [--pin XXXX] [--loopback]

不带 token（--open 关闭）时自动生成 6 位访问码并拼进 URL。
Ctrl+C 退出。
"""

from __future__ import annotations

import argparse
import secrets
import socket
import sys
import time
import webbrowser
from pathlib import Path

from landrop import __version__
from landrop import mdns
from landrop.net import lan_ip, print_qrcode
from landrop.server import make_receive_server, make_share_server, make_text_server


def _gen_token(explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    return secrets.token_hex(3)   # 6 位十六进制，方便手机输入


def _start_mdns(args, mode: str, port: int, token: str):
    """启动 mDNS 公告。返回 Announcer 或 None（--no-mdns/--loopback/端口被占）。"""
    if getattr(args, "no_mdns", False) or getattr(args, "loopback", False):
        return None
    inst = (getattr(args, "name", "") or socket.gethostname().split(".")[0]).strip()
    ann = mdns.Announcer(
        inst, port,
        {"dv": "landrop-lite", "ver": __version__, "mode": mode,
         "open": "1" if not token else "0"},
        lan_ip(),
    )
    if ann.start():
        print(f"mDNS 公告中：{inst}（{mode}，端口 {port}）"
              + ("" if ann.available else "（不可用）"))
        return ann
    print("提示: mDNS 端口(5353)被占用或被防火墙拦截，跳过自动发现（功能不受影响）。")
    return None


def cmd_share(args) -> int:
    target = Path(args.path).absolute()
    if not target.exists():
        print(f"错误: 路径不存在: {target}", file=sys.stderr)
        return 1
    token = "" if args.open else _gen_token(args.pin)
    host = "127.0.0.1" if args.loopback else "0.0.0.0"
    server = make_share_server(str(target), token=token, host=host, port=args.port)
    ann = _start_mdns(args, "share", args.port, token)
    base = f"http://{lan_ip()}:{args.port}"
    url = f"{base}/s/{token}" if token else f"{base}/s/"
    print(f"共享: {target}")
    print(f"局域网下载链接: {url}")
    if args.loopback:
        print("(仅本机可访问 --loopback)")
    if token:
        print(f"访问码: {token}  （换设备访问需带完整链接或 ?pin={token}）")
    if not print_qrcode(url):
        print("提示: 安装 qrcode 包可在终端显示二维码 -> pip install qrcode")
    print("Ctrl+C 停止共享。")
    if not args.no_browser:
        webbrowser.open(f"http://127.0.0.1:{args.port}/s/{token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
        if ann:
            ann.stop()
    return 0


def cmd_receive(args) -> int:
    out = Path(args.out).absolute()
    out.mkdir(parents=True, exist_ok=True)
    token = "" if args.open else _gen_token(args.pin)
    host = "127.0.0.1" if args.loopback else "0.0.0.0"
    browser = None
    if not args.no_mdns and not args.loopback:
        browser = mdns.Browser()
        browser.start()
    server = make_receive_server(str(out), token=token, host=host,
                                 port=args.port, browser=browser)
    ann = _start_mdns(args, "receive", args.port, token)
    base = f"http://{lan_ip()}:{args.port}"
    url = f"{base}/?pin={token}" if token else f"{base}/"
    print(f"接收目录: {out}")
    print(f"局域网上传页面: {url}")
    if args.loopback:
        print("(仅本机可访问 --loopback)")
    if token:
        print(f"访问码: {token}")
    if not print_qrcode(url):
        print("提示: 安装 qrcode 包可在终端显示二维码 -> pip install qrcode")
    print("Ctrl+C 停止接收。")
    if not args.no_browser:
        webbrowser.open(f"http://127.0.0.1:{args.port}/?pin={token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
        if ann:
            ann.stop()
        if browser:
            browser.stop()
    return 0


def cmd_text(args) -> int:
    """把一段文本发布到局域网，手机打开即看、一键复制。"""
    if args.clip:
        import subprocess
        try:
            text = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True, timeout=10, check=True,
            ).stdout.decode("utf-8", "replace").rstrip("\r\n")
        except (OSError, subprocess.SubprocessError) as e:
            print(f"错误: 读取剪贴板失败({e})。可改用直接传文本或 --stdin。", file=sys.stderr)
            return 1
    elif args.stdin:
        print("输入/粘贴文本，Ctrl+Z 回车结束（Windows）：")
        text = sys.stdin.read()
    else:
        text = " ".join(args.text) if args.text else ""
    if not text.strip():
        print("错误: 文本为空。", file=sys.stderr)
        return 1

    token = "" if args.open else _gen_token(args.pin)
    host = "127.0.0.1" if args.loopback else "0.0.0.0"
    server = make_text_server(text, token=token, host=host, port=args.port)
    ann = _start_mdns(args, "text", args.port, token)
    base = f"http://{lan_ip()}:{args.port}"
    url = f"{base}/s/{token}" if token else f"{base}/s/"
    print(f"文本长度: {len(text)} 字符")
    print(f"局域网链接: {url}")
    if token:
        print(f"访问码: {token}")
    if not print_qrcode(url):
        print("提示: 安装 qrcode 包可在终端显示二维码 -> pip install qrcode")
    print("Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
        if ann:
            ann.stop()
    return 0


def cmd_discover(args) -> int:
    """浏览局域网里的 LANDrop 设备（mDNS 查询 N 秒后打印结果）。"""
    browser = mdns.Browser()
    if not browser.start():
        print("错误: mDNS 端口(5353)被占用或被防火墙拦截，无法发现。")
        return 2
    print(f"发现中（{args.seconds} 秒）… Ctrl+C 提前结束。")
    try:
        deadline = time.time() + args.seconds
        while time.time() < deadline:
            browser.refresh()
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    peers = browser.peers()
    browser.stop()
    if not peers:
        print("没有发现设备。检查：双方都在网内且 LANDrop 开着；Windows 防火墙放行 Python。")
        return 0
    for p in peers:
        mode = p.get("txt", {}).get("mode", "")
        lock = "开放" if p.get("txt", {}).get("open") == "1" else "需访问码"
        print(f"  {p['instance']}  {p['ip']}:{p['port']}  [{mode or '?'}·{lock}]")
    return 0


def _add_mdns_args(p) -> None:
    p.add_argument("--name", help="在局域网里显示的设备名（默认计算机名）")
    p.add_argument("--no-mdns", action="store_true", help="关闭 mDNS 自动发现")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
    parser = argparse.ArgumentParser(
        prog="landrop", description="LANDrop-lite — 局域网文件传输（纯标准库）")
    parser.add_argument("--version", action="version", version=f"LANDrop {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("share", help="共享文件/目录给局域网下载")
    p.add_argument("path")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--pin", help="自定义访问码（默认随机生成）")
    p.add_argument("--open", action="store_true", help="不加访问码（任何人可下载）")
    p.add_argument("--loopback", action="store_true", help="仅本机可访问")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    _add_mdns_args(p)
    p.set_defaults(func=cmd_share)

    p = sub.add_parser("receive", help="开启上传页面接收文件")
    p.add_argument("--out", default="LANDrop接收", help="接收目录（默认 ./LANDrop接收）")
    p.add_argument("--port", type=int, default=8801)
    p.add_argument("--pin", help="自定义访问码（默认随机生成）")
    p.add_argument("--open", action="store_true", help="不加访问码")
    p.add_argument("--loopback", action="store_true", help="仅本机可访问")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    _add_mdns_args(p)
    p.set_defaults(func=cmd_receive)

    p = sub.add_parser("text", help="把一段文本发布到局域网（手机扫码查看/复制）")
    p.add_argument("text", nargs="*", help="要分享的文本（或用 --stdin / --clip）")
    p.add_argument("--stdin", action="store_true", help="从标准输入读取文本")
    p.add_argument("--clip", action="store_true", help="读取 Windows 剪贴板内容")
    p.add_argument("--port", type=int, default=8802)
    p.add_argument("--pin", help="自定义访问码（默认随机生成）")
    p.add_argument("--open", action="store_true", help="不加访问码")
    p.add_argument("--loopback", action="store_true", help="仅本机可访问")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    _add_mdns_args(p)
    p.set_defaults(func=cmd_text)

    p = sub.add_parser("discover", help="发现局域网里的 LANDrop 设备")
    p.add_argument("--seconds", type=int, default=5, help="发现窗口秒数")
    p.set_defaults(func=cmd_discover)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
