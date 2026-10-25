"""LANDrop mDNS/DNS-SD 自动发现（零依赖实现，RFC 6762/6763 子集）。

服务名：_landrop._tcp.local。
设计约束（来自沙箱实测，DECISIONS D17）：
- 包构建/解析是纯函数（合成字节串测试）；
- 同进程 announce+browse 集成测试可在任何环境跑；
- 跨进程组播在 ZCode 沙箱内被阻断 → 真机双进程验证留给用户控制台。
- 5353 端口被其他 mDNS 栈独占时优雅降级（discover 返回 None，产品回到手动输入模式）。
"""

from __future__ import annotations

import socket
import struct
import threading
import time

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SERVICE_TYPE = "_landrop._tcp.local"
TTL_CACHE_SECONDS = 30.0

# RR 类型
TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33


# ---------- 名字编解码（纯函数） ----------

def encode_name(name: str) -> bytes:
    """'a.b.local' → \\x01a\\x01b\\x05local\\x00。支持转义点（\\.)。"""
    out = bytearray()
    if name in ("", "."):
        return b"\x00"
    for label in _split_labels(name):
        raw = label.encode("utf-8")
        if not 0 < len(raw) < 64:
            raise ValueError(f"bad label: {label!r}")
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def _split_labels(name: str) -> list[str]:
    labels, cur, esc = [], [], False
    for ch in name.rstrip("."):
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ".":
            labels.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    labels.append("".join(cur))
    return [l for l in labels if l]


def decode_name(buf: bytes, off: int) -> tuple[str, int]:
    """解析（可能带压缩指针的）名字，返回 (name, 下一偏移)。跳数上限防环。

    标签内的点/反斜杠按 DNS 惯例转义输出，保证与 encode_name 往返一致。
    """
    labels: list[str] = []
    jumps = 0
    pos = off
    end = -1
    while True:
        if pos >= len(buf):
            raise ValueError("name out of range")
        length = buf[pos]
        if length == 0:
            if end < 0:
                end = pos + 1
            break
        if length & 0xC0 == 0xC0:  # 压缩指针
            if pos + 1 >= len(buf):
                raise ValueError("truncated pointer")
            ptr = ((length & 0x3F) << 8) | buf[pos + 1]
            if end < 0:
                end = pos + 2
            jumps += 1
            if jumps > 16 or ptr >= pos:
                raise ValueError("bad compression pointer")
            pos = ptr
            continue
        pos += 1
        if pos + length > len(buf):
            raise ValueError("label out of range")
        raw = buf[pos:pos + length].decode("utf-8", errors="replace")
        labels.append(raw.replace("\\", "\\\\").replace(".", "\\."))
        pos += length
    return ".".join(labels) + "." if labels else ".", end


# ---------- 包构建（纯函数） ----------

def build_query(name: str = SERVICE_TYPE, qtype: int = TYPE_PTR,
                qid: int = 0) -> bytes:
    """单问题查询包（mDNS 规范：id=0，不要求递归）。"""
    header = struct.pack(">HHHHHH", qid, 0, 1, 0, 0, 0)
    return header + encode_name(name) + struct.pack(">HH", qtype, 1)


def _rr(name: str, rtype: int, rdata: bytes, ttl: int = 120,
        cache_flush: bool = True) -> bytes:
    cls = 0x8001 if cache_flush else 0x0001  # mDNS cache-flush 位
    return (encode_name(name) + struct.pack(">HHIH", rtype, cls, ttl, len(rdata))
            + rdata)


def build_response(instance: str, port: int, txt: dict[str, str],
                   ip: str, host_label: str, ttl: int = 120) -> bytes:
    """DNS-SD 应答：PTR + SRV + TXT + A，单包 uncompressed（合法）。"""
    inst_fqdn = f"{instance}.{SERVICE_TYPE}"
    host_fqdn = f"{host_label}.local"
    header = struct.pack(">HHHHHH", 0, 0x8400, 0, 4, 0, 0)  # QR|AA
    ptr_rdata = encode_name(inst_fqdn)
    srv_rdata = struct.pack(">HHH", 0, 0, port) + encode_name(host_fqdn)
    txt_rdata = b"".join(
        bytes([min(len(s), 255)]) + s[:255]
        for s in (f"{k}={v}".encode("utf-8") for k, v in sorted(txt.items()))
    ) or b"\x00"
    a_rdata = socket.inet_aton(ip)
    return (header
            + _rr(SERVICE_TYPE, TYPE_PTR, ptr_rdata, ttl, cache_flush=False)
            + _rr(inst_fqdn, TYPE_SRV, srv_rdata, ttl)
            + _rr(inst_fqdn, TYPE_TXT, txt_rdata, ttl)
            + _rr(host_fqdn, TYPE_A, a_rdata, ttl))


# ---------- 包解析（纯函数） ----------

def parse_packet(data: bytes) -> dict:
    """解析 mDNS 包 → {is_response, questions:[], answers:[]}。

    坏包返回尽可能多的已解析内容，绝不抛异常（健壮性优先）。
    """
    out: dict = {"is_response": False, "questions": [], "answers": []}
    if len(data) < 12:
        return out
    flags = struct.unpack_from(">H", data, 2)[0]
    qd, an = struct.unpack_from(">HH", data, 4)[0], struct.unpack_from(">HH", data, 6)[0]
    out["is_response"] = bool(flags & 0x8000)
    off = 12
    try:
        for _ in range(qd):
            name, off = decode_name(data, off)
            if off + 4 > len(data):
                return out
            qtype = struct.unpack_from(">H", data, off)[0]
            off += 4
            out["questions"].append({"name": name, "type": qtype})
        for _ in range(an):
            name, off = decode_name(data, off)
            if off + 10 > len(data):
                return out
            rtype, _cls, ttl, rdlen = struct.unpack_from(">HHIH", data, off)
            off += 10
            rdata = data[off:off + rdlen]
            off += rdlen
            rec: dict = {"name": name, "type": rtype, "ttl": ttl}
            if rtype == TYPE_PTR and rdata:
                rec["target"], _ = decode_name(rdata, 0)
            elif rtype == TYPE_SRV and len(rdata) >= 6:
                rec["port"] = struct.unpack_from(">H", rdata, 4)[0]
                rec["server"], _ = decode_name(rdata, 6)
            elif rtype == TYPE_TXT:
                rec["txt"] = _parse_txt(rdata)
            elif rtype == TYPE_A and len(rdata) == 4:
                rec["ip"] = socket.inet_ntoa(rdata)
            out["answers"].append(rec)
    except (ValueError, struct.error):
        return out  # 截断/坏包：返回已解析部分
    return out


def _parse_txt(rdata: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    pos = 0
    while pos < len(rdata):
        ln = rdata[pos]
        pos += 1
        if ln == 0 or pos + ln > len(rdata):
            break
        kv = rdata[pos:pos + ln].decode("utf-8", errors="replace")
        pos += ln
        k, _, v = kv.partition("=")
        if k:
            out[k] = v
    return out


def peers_from_packet(parsed: dict) -> dict | None:
    """从应答包提取一个 peer 信息；不完整（缺 SRV/A）返回 None。"""
    if not parsed.get("is_response"):
        return None
    inst = srv = txt = addr = None
    for a in parsed["answers"]:
        if a["type"] == TYPE_SRV:
            inst, srv = a["name"].removesuffix("." + SERVICE_TYPE + "."), a
        elif a["type"] == TYPE_TXT:
            txt = a.get("txt", {})
        elif a["type"] == TYPE_A:
            addr = a.get("ip")
    if not (inst and srv and addr):
        return None
    return {
        "instance": inst,
        "ip": addr,
        "port": srv.get("port", 0),
        "hostname": srv.get("server", ""),
        "txt": txt or {},
    }


# ---------- 运行时（线程） ----------

def _mcast_socket(ttl: int = 1) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    return s


class Announcer:
    """被动应答 + 主动广播自己的服务（mDNS 风格：启动时 1s/2s/4s 三连发）。"""

    def __init__(self, instance: str, port: int, txt: dict[str, str],
                 ip: str, host_label: str | None = None) -> None:
        self.instance = instance[:60] or "landrop"
        self.port = port
        self.txt = txt
        self.ip = ip
        self.host_label = (host_label or socket.gethostname().split(".")[0])[:60]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.available = False  # 绑定失败置 False

    def start(self) -> bool:
        try:
            self.sock = _mcast_socket()
            self.sock.bind(("", MDNS_PORT))
            mreq = struct.pack("4sL", socket.inet_aton(MDNS_GROUP), socket.INADDR_ANY)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            self.available = False
            return False
        self.available = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="landrop-mdns-announce")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        resp = build_response(self.instance, self.port, self.txt,
                              self.ip, self.host_label)
        # 主动公告：0s / 1s / 2s（间隔指数）+ 之后每 15s 保底
        next_announce = 0.0
        announces_left = 3
        last_announce = 0.0
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            now = time.monotonic()
            if announces_left > 0 and now >= next_announce:
                try:
                    self.sock.sendto(resp, (MDNS_GROUP, MDNS_PORT))
                except OSError:
                    pass
                announces_left -= 1
                last_announce = now
                next_announce = now + (1 << announces_left) if announces_left else now + 15
            elif announces_left == 0 and now - last_announce >= 15:
                try:
                    self.sock.sendto(resp, (MDNS_GROUP, MDNS_PORT))
                except OSError:
                    pass
                last_announce = now
            try:
                data, _addr = self.sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            if self._stop.is_set():
                break
            pkt = parse_packet(data)
            if pkt["is_response"] or len(pkt["questions"]) != 1:
                continue
            q = pkt["questions"][0]
            if q["name"] == SERVICE_TYPE + "." and q["type"] in (TYPE_PTR, 255):
                try:
                    self.sock.sendto(resp, (MDNS_GROUP, MDNS_PORT))
                except OSError:
                    pass
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """发送 PTR 查询并缓存应答；peers() 返回 TTL 内的活跃实例。"""

    def __init__(self) -> None:
        self._peers: dict[str, dict] = {}  # instance → peer + seen 时间
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.available = False

    def start(self) -> bool:
        try:
            self.sock = _mcast_socket()
            self.sock.bind(("", MDNS_PORT))
            mreq = struct.pack("4sL", socket.inet_aton(MDNS_GROUP), socket.INADDR_ANY)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            self.available = False
            return False
        self.available = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="landrop-mdns-browse")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()   # socket 由 _loop 线程自己在退出时关闭（单属主，避免跨线程关停竞争）

    def refresh(self) -> None:
        try:
            self.sock.sendto(build_query(), (MDNS_GROUP, MDNS_PORT))
        except OSError:
            pass

    def peers(self, max_age: float = TTL_CACHE_SECONDS) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            dead = [k for k, v in self._peers.items() if now - v["_seen"] > max_age]
            for k in dead:
                del self._peers[k]
            return [{k: v for k, v in p.items() if not k.startswith("_")}
                    for p in sorted(self._peers.values(), key=lambda x: x["instance"])]

    def _loop(self) -> None:
        self.refresh()
        last_query = time.monotonic()
        self.sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                data, _addr = self.sock.recvfrom(9000)
            except socket.timeout:
                if time.monotonic() - last_query >= 10:
                    self.refresh()
                    last_query = time.monotonic()
                continue
            except OSError:
                break
            peer = peers_from_packet(parse_packet(data))
            if not peer or peer["instance"] == "_landrop":
                continue
            with self._lock:
                peer["_seen"] = time.monotonic()
                self._peers[peer["instance"]] = peer
        try:
            self.sock.close()
        except OSError:
            pass
