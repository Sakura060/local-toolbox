"""Probe v3: cross-process multicast with explicit interface + loop.

Usage: python probe_multicast3.py rx|tx [if_ip]
"""
import socket
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GROUP = "224.0.0.251"
PORT = 55357

mode = sys.argv[1]
if_ip = sys.argv[2] if len(sys.argv) > 2 else None

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

if mode == "rx":
    s.bind(("", PORT))
    mreq = struct.pack("4sL", socket.inet_aton(GROUP), socket.INADDR_ANY)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    s.settimeout(6)
    try:
        data, addr = s.recvfrom(2048)
        print(f"PASS {len(data)}B from {addr}: {data[:20]!r}")
    except socket.timeout:
        print("FAIL timeout")
else:
    if if_ip:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                     socket.inet_aton(if_ip))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.sendto(b"probe3-xproc", (GROUP, PORT))
    print("sent")
s.close()
