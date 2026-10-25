"""Probe: does UDP multicast (mDNS-style) work inside the sandbox?

One process: two sockets (A announces, B joins group and receives on loopback
+ wildcard). Prints PASS/FAIL lines; exits 0 either way.
"""
import socket
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GROUP = "224.0.0.251"
PORT = 55355  # 非标准端口做探针，不碰系统 mDNS


def rx():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", PORT))
    except OSError as e:
        print(f"bind fail: {e}")
        return
    mreq = struct.pack("4sL", socket.inet_aton(GROUP), socket.INADDR_ANY)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        print("join ok")
    except OSError as e:
        print(f"join fail: {e}")
    s.settimeout(3)
    try:
        data, addr = s.recvfrom(2048)
        print(f"PASS recv {len(data)}B from {addr}: {data[:20]!r}")
    except socket.timeout:
        print("FAIL recv timeout")
    finally:
        s.close()


def tx():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(3)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.sendto(b"landrop-mdns-probe", (GROUP, PORT))
        print("send ok")
    except OSError as e:
        print(f"send fail: {e}")
    finally:
        s.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "rx"
    (rx if mode == "rx" else tx)()
