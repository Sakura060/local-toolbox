# ClipVault 剪贴板时间线

剪贴板的本地时间线：**复制过的文本 / 截图 / 文件自动记录，可搜索、按类型过滤、一键回贴（原格式）**。全部数据只落本机，绝不上传。

## 启动

双击 `启动剪贴板.bat`（或 `python cli.py serve`）→ http://127.0.0.1:8643
采集线程每 1.5 秒看一眼剪贴板序列号，有变化即入库（去重上浮：同内容再次复制会浮到最顶）。

## 能做什么

1. **时间线**：按天分组的条目流，📝 文本 / 🖼️ 图片 / 📁 文件三类，相对时间 + 大小；置顶的排最前。
2. **搜索**：输入即搜（防抖），按 body 子串匹配；类型 chips（全部/文本/图片/文件）。
3. **回贴（v0.2 起按原格式）**：文本 → CF_UNICODETEXT；**图片 → CF_DIB 位图**（粘贴得到图像本身，不再是文件路径）；**文件 → CF_HDROP 文件列表**（资源管理器/聊天窗口按文件粘贴）。
4. **深链定位（v0.2）**：URL 支持 `?q=<过滤词>&focus=<条目id>`——从 ToolHub 联邦搜索点进来时自动过滤、滚动到条目并高亮闪一下；条目不在结果里会明确提示。
5. **隐私三开关**：⏸ 暂停采集（暂停期间的复制不入库）/ 单条删除 / 一键清空（置顶豁免）。
6. **滚动保留**：未置顶条目最多 300 条（`store.RETENTION_DEFAULT`），图片文件随条目删除。

## 快捷键 / CLI

```bat
python cli.py pause | resume     暂停/恢复采集
python cli.py status             条数统计
python cli.py clear [--all]      清空（--all 连置顶一起）
python cli.py read-once [--show] 读一次剪贴板（默认只报类型/长度，隐私保护）
python cli.py serve --no-capture 仅查看模式（不起采集线程）
python cli.py install-autostart  开机自启（Startup 放 VBS，隐藏窗口常驻；uninstall-autostart 移除）
```

## 架构

```
clipvault/
  store.py   SQLite 时间线（去重上浮/置顶/保留上限/暂停开关，线程安全 + close()）
  clip.py    采集（PIL ImageGrab.grabclipboard 一站式读取 + GetClipboardSequenceNumber
             轮询）与回贴三格式（CF_UNICODETEXT / CF_DIB / CF_HDROP，公共 _set_clipboard_data）；
             png_to_dib / build_hdrop 是纯函数（字节级可测），dispatch 纯函数可测
  server.py  make_server 工厂（store/images 注入，测试不碰真实剪贴板）
  page.html  时间线单页（设计令牌 + 深色模式、esc 全量、深链 flash 高亮 + reduced-motion 降级）
```

数据落盘：`data\vault.db`（WAL）+ `data\images\*.png`。

## 测试

`python -m unittest discover -s tests`（60 个）——store 去重上浮/retention 置顶豁免/搜索/
暂停/删除连带图片文件；回贴字节构造（DIB 头/底朝上 BGRA/HDROP 双 NUL）+ 假 OS 层全流程
（格式号/落内存字节/失败路径）；图片回采感知去重（dHash/窗口/有符号往返）；server 全端点
（回贴 mock 掉真实剪贴板、分类型断言）；自启接线；页面资产断言（含深链）。
工具：`tools\verify_dib.py`（BMP 解码交叉验证像素）、
`tools\seed_preview.py <port>` + `tools\pw_deeplink_e2e.py`（无头 Edge 深链 E2E）。
端到端：`python tools\e2e_server.py <本项目目录> 8896 ClipVault serve --port 8896 --no-browser --no-capture`。

## 已知边界（v0.2）

- CF_DIB 用 32bpp BI_RGB：粘贴到老式画图等只认 24bpp 的程序会丢透明度（截图场景不透明，无感）。
- 1.5s 轮询意味着极快连复制再粘贴的内容可能错过（序列号跳变只取最后一次）。
- 已接入 ToolHub 联邦搜索（v0.2 起点击条目新标签直达本页深链，自动过滤 + 高亮）。
