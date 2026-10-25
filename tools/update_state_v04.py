"""Append D17 to DECISIONS.md and this round's entry to DEV_LOG.md."""
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "DECISIONS.md"
t = d.read_text(encoding="utf-8")
t += """
## D17 LANDrop mDNS 实现与组播实证（2026-09-27）
零依赖手写 DNS-SD 子集（landrop/mdns.py）：服务 _landrop._tcp.local；TXT 只带 dv/ver/mode/open，**访问码永不广播**（需码设备在面板只显示地址，用户手动输入）。真机实证：同进程 announce→browse 回环 OK；**跨进程公告→discover 发现也 OK（走默认路由选卡）**；但强制 `IP_MULTICAST_IF=192.168.230.1`（某虚拟网卡）会吞包——组播一律不要手动指定接口，除非用户明确要求。5353 被占/防火墙拦截时优雅降级为手动模式。测试端口注意：LANDrop 用 8893-8895，8890 与 NoteNest 常驻端口冲突过（症状：全 404，其实是打到别的服务上了）。
"""
d.write_text(t, encoding="utf-8")

p = Path(__file__).resolve().parent.parent / "DEV_LOG.md"
t = p.read_text(encoding="utf-8")
t += """
[01:00-01:50] LANDrop v0.4 mDNS 自动发现 + 户口本/消化器/NoteNest v0.2 收尾（续）
完成：
  - LANDrop v0.4：新模块 landrop/mdns.py（DNS-SD 名字编解码/包构建解析/压缩指针/Announcer 三连发+被动应答/Browser TTL 缓存）；share/receive/text 自动公告（--name/--no-mdns）；receive 挂 Browser + /api/peers + 页面设备面板；新命令 discover。72 测试全绿。
  - 真机验证：后台 receive（公告）→ 前台 discover 3s 发现 Sakura 192.168.1.35:8897——跨进程组播全链路通；浏览器实测设备面板渲染（设备名/IP/模式/开放状态）。
  - 测试基础设施：ShareServerTest 端口 8890→8895（与 NoteNest 常驻端口冲突导致全 404 假阳性）。
发现：
  1. 组播探针三连：跨进程组播最初"失败"实为强制 IP_MULTICAST_IF 指到 192.168.230.1 虚拟网卡吞包；默认路由选卡则通（D17）
  2. mdns 三个真 bug 被单测抓住：TXT rdata 漏逐串长度前缀（RFC 1035）；decode_name 未反转义标签内点；压缩指针测试自身偏移写错
  3. stop() 跨线程关 socket 产生 10038 噪音 → 改为 _loop 线程退出时自关（单属主）
测试：LANDrop 72 ✅；真机 discover 跨进程发现 ✅；面板渲染 ✅。
状态：PROJECT_STATE/TODO/DEV_LOG/DECISIONS(D17) 同步完成。
"""
p.write_text(t, encoding="utf-8")
print("DECISIONS + DEV_LOG updated")
