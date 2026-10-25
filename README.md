# ToolHub — 本地工具箱 | Local-first Windows Toolbox

**一个窗口装下一整套本地工具，还能把电脑上任意软件登记进来当插件——数据全部留在本机。**

## 中文说明

ToolHub 桌面版 v0.3.0：10 个纯 Python 本地工具 + 统一桌面入口（原生窗口），
外加"万物皆插件"系统——把电脑上的 exe / 快捷方式 / 脚本 / 文件夹 / 网址 / Windows 商店应用
登记进工具箱一键启动，支持命令行参数模板、批量扫描导入、清单分享迁移、工作场景组合
（一键开一整组工具）。**不联网上传、无账号、无遥测**；全部 HTTP 服务只监听 127.0.0.1。

### 内置 10 工具

| 工具 | 一句话 |
|---|---|
| FileFlow 文件整理器 | 乱目录自动归类（预览→执行→可撤销、重复文件三级查重） |
| LANDrop-lite | 电脑↔手机同一 Wi-Fi 互传文件 |
| NoteNest 本地笔记 | Markdown 笔记，截图直接粘贴、一键上色、自动保存 |
| 捞图 截图记忆库 | 搜截图里的文字 + 时间线 + 相似查重 |
| 文件户口本 | 查"这个文件从哪来"（来源归因/谱系链） |
| B站收藏消化器 | 视频转文字重点（本地转写，零 API 费用） |
| 文档户口本 | 同源文档自动聚谱系树，任意两版对比 |
| 外发卫士 | 发文件前查隐私泄露，一键净化另存 |
| ClipVault | 剪贴板时间线，按原格式一键回贴 |
| 番茄钟 | 专注计时（apps/ 示范插件） |

### 快速开始（Windows 10/11 + Python 3.10+）

```
git clone <本仓库>
cd ToolHub-工具箱门户
python cli.py desktop        # 打开桌面版窗口（或双击 启动桌面版.bat）
```

每个工具也可以独立运行（进入各自目录双击 .bat）。无云服务依赖；首次使用 B站收藏消化器
转写时会联网下载一次语音模型（约 150MB），其余全部离线。

### 测试

```
python tools\run_all_tests.py     # 全仓库 11 个项目、657 个测试（约 2 分钟）
```

### 目录结构

- `ToolHub-工具箱门户/` —— 桌面版 + 旧网页门户（desktop/ 包为核心：四层注册表、
  插件系统、工作场景、环境自检；docs/ 里有架构与 API 对接文档）
- 各工具目录 / `localcore/`（共享件）/ `apps/`（第三方示范插件）
- `tools/` —— 构建/测试辅助脚本（打包、全量测试、发布树生成）

## English

ToolHub Desktop v0.3.0: 10 local-first Python tools behind one native
Windows window, plus a plugin system that registers any local exe / shortcut /
script / folder / URL / Microsoft Store app as a launchable card — with
command-line argument templates, bulk directory scan import, shareable plugin
manifests, and work scenes (launch a whole group in one click).

**No telemetry, no accounts, no cloud sync.** All HTTP services bind to
127.0.0.1 only. Architecture and API docs live in
`ToolHub-工具箱门户/docs/` (Chinese; the code and tests are the source of truth).

## License

MIT — see [LICENSE](LICENSE).
