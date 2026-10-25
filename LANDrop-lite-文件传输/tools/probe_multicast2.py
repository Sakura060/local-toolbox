"""Probe v2: single-process multicast loopback (threads), bind variants."""
import socket
import struct
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GROUP = "224.0.0.251"
PORT = 55356


def try_variant(bind_to, loop, label):
    got = []

    def rx():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((bind_to, PORT))
            mreq = struct.pack("4sL", socket.inet_aton(GROUP), socket.INADDR_ANY)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            s.settimeout(2)
            data, addr = s.recvfrom(2048)
            got.append((addr, data))
        except OSError as e:
            got.append(("err", str(e)))
        finally:
            s.close()

    t = threading.Thread(target=rx, daemon=True)
    t.start()
    time.sleep(0.3)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1 if loop else 0)
        s.sendto(b"probe2", (GROUP, PORT))
    except OSError as e:
        print(f"{label}: send fail {e}")
        s.close()
        return
    finally:
        pass
    t.join(timeout=3)
    s.close()
    print(f"{label}: {'OK ' + repr(got[0]) if got else 'no delivery'}")


try_variant("", True, "bind-any + loop-on")
try_variant(GROUP, True, "bind-group + loop-on")
# 对照：普通 UDP 单播回环
s1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s1.bind(("127.0.0.1", 0))
s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s2.sendto(b"uni", s1.getsockname())
s1.settimeout(2)
try:
    print("unicast control:", s1.recvfrom(100))
except socket.timeout:
    print("unicast control: FAIL (even unicast blocked?!)")
