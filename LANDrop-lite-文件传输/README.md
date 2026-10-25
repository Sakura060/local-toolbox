# LANDrop-lite 局域网文件传输

同一 Wi-Fi/局域网内，电脑与手机/平板互传文件。**纯 Python 标准库，零第三方依赖**。

四个模式：

- **share** —— 电脑把文件/目录共享出去，手机浏览器打开链接即可下载
- **receive** —— 电脑开启上传页，手机浏览器打开即可把照片等文件传到电脑
- **text** —— 文本快传，手机扫码即看即复制
- **discover** —— 发现局域网里的 LANDrop 设备（mDNS 自动发现）

## 快速开始

```bat
:: 把一个目录共享给局域网下载
python cli.py share "D:\照片备份"

:: 开启上传接收页（文件落在 LANDrop接收\ 目录）
python cli.py receive

:: 文本快传：把一段话发到手机（剪贴板/参数/标准输入）
python cli.py text --clip
python cli.py text "会议纪要：明天上午 10 点评审"

:: 看看局域网里还有谁开着 LANDrop
python cli.py discover
```

终端会打印局域网链接（形如 `http://192.168.1.5:8800/s/a1b2c3`）和访问码；安装 `qrcode` 包后同时显示终端二维码，手机扫码即达。

选项：

| 选项 | 说明 |
|---|---|
| `--port N` | 自定义端口（share 默认 8800，receive 默认 8801） |
| `--pin XXX` | 自定义访问码（默认随机生成 6 位） |
| `--open` | 不加访问码（任何连入局域网的设备都能访问） |
| `--loopback` | 仅本机可访问（调试用，同时关闭 mDNS） |
| `--out 目录` | receive 的接收目录 |
| `--name 设备名` | 在局域网里显示的名字（默认计算机名） |
| `--no-mdns` | 关闭 mDNS 自动发现 |

## mDNS 自动发现（v0.4）

零依赖手写 DNS-SD（RFC 6762/6763 子集，`landrop/mdns.py`）：服务 `_landrop._tcp.local`，
announce（1s/2s/4s 三连发 + 15s 保底）+ 被动应答 PTR 查询；browser 发查询、缓存 30s TTL。

- share/receive/text 启动即自动公告（`--no-mdns` 关闭）；**首次运行 Windows 防火墙弹窗请放行**
- receive 上传页底部"📡 局域网里的 LANDrop 设备"面板（4s 轮询 `/api/peers`），点击直达对端；
  需访问码的设备只显示地址、打开后手动输入访问码——**访问码永不广播**
- 5353 被其他 mDNS 栈占用时优雅降级为手动模式，功能不受影响

## 安全设计

- **访问码即能力**：share 的链接路径自带随机 token，receive 需要 `?pin=`；猜不到就进不来
- **上传文件名多重清洗**：剥路径成分（`../`、盘符）、过滤 Windows 非法字符与保留设备名（CON/NUL/COM1…）、限长保留扩展名
- **落盘前二次校验**：`pathlib.resolve` + 父目录检查，即使清洗被绕过也写不出接收目录
- **分块上传顺序校验**：块号必须连续（乱序 409），临时文件限于接收目录内 `.landrop-uploads/`，1 小时无进展自动清理；重名自动追加 (1)(2) 不覆盖
- **体积上限**：整包上传 2GB（兼容旧端点）；分块上传 10GB、单块 ≤8MB，服务端流式落盘不占内存
- **错误信息不泄漏服务器路径**

## 分块上传协议（页面自动使用，也可脚本调用）

```
POST /upload-init   {"filename": "...", "size": N}
             → {"id": "...", "chunk_size": 4194304, "total_chunks": K}
POST /upload-chunk?id=<id>&n=<0..K-1>   body = 原始字节（顺序发送）
             → {"received": n+1, "total_chunks": K}
POST /upload-finish {"id": "..."}       → {"saved": "最终文件名", "size": N}
POST /upload-abort  {"id": "..."}       取消并清理临时文件
```

`tools/verify_chunked.py` 是一份可运行的协议验证脚本。

## 测试

```bat
python -m unittest discover -s tests    :: 72 个测试
```

覆盖：文件名清洗、multipart 解析、服务器全链路、分块协议、mDNS 名字编解码（转义/压缩指针/防环）、包构建解析往返（含畸形输入永不抛异常）、同进程 announce→browse 集成、/api/peers 端点。

## 目录结构

```
landrop/
  server.py       Share/Receive/Text HTTP 服务 + 内嵌页面 + 分块上传 + /api/peers
  mdns.py         mDNS/DNS-SD 自动发现（零依赖实现）
  multipart.py    multipart/form-data 解析器
  securenames.py  文件名清洗与路径安全
  net.py          局域网 IP 探测 / 终端二维码（可选）
cli.py            命令行入口（share/receive/text/discover）
tools/            verify_chunked.py + probe_multicast*.py 组播探针
tests/            单元 + 集成测试
```

## 已验证

- 72 个自动化测试全过
- **mDNS 真机验证（2026-09-27）**：后台起 receive（mDNS 公告）→ 前台 `discover` 3 秒发现 "Sakura 192.168.1.35:8897 [receive·开放]"——跨进程组播公告→查询→解析全链路；浏览器实测接收页设备面板渲染（设备名/IP/模式/访问码状态）
- 断点续传真机验证：206 分块下载拼接逐字节一致；HEAD/416 正确
- 分块上传真机验证：10MB×3 块上传字节级一致；浏览器确认新脚本绑定
- 浏览器实测：上传页/下载页/文本页渲染、计算样式校验、截图验收

## 版本

- v0.1 share/receive 双模式 + 访问码 + 安全清洗 + Web 页面
- v0.2 share 下载支持 **Range 断点续传**（206/416）与 HEAD；新增 **text 文本快传**（--clip 读剪贴板、/raw 纯文本端点）
- v0.3 **分块上传**：init/chunk/finish/abort 四端点，上限提升至 10GB，流式落盘零内存压力；页面统一走分块路径并保留 /upload 兼容
- v0.4 **mDNS 自动发现**：零依赖 DNS-SD 实现，share/receive/text 自动公告 + discover 命令 + receive 页设备面板；访问码不进 TXT 记录
- 未来想法：逐文件进度、WebDAV 兼容层
