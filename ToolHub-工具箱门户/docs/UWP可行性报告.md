# UWP / Windows 商店应用导入 · 可行性实测报告

> 2026-10-25 ｜ 实测环境：本机 Windows 11（10.0.26300），PowerShell 5.1
> 依据：《ToolHub v0.2.0 现状审计与后续迭代开发总提示词》第六节——先实测再编码。
> 原始数据：`tools/uwp_feasibility_raw.txt`（脚本 `tools/uwp_feasibility.py` 可复跑）。

## 测试环境与方法

- 枚举：`Get-StartApps`（PowerShell，无需管理员）
- UWP 识别：AppID 含 `!`（包族名!入口名），普通 .lnk 不含
- 启动：`Start-Process 'shell:appsFolder\<AUMID>'`（与产品代码同路径 `os.startfile`）
- **测试范围最小化：只对系统自带「计算器」做真实启动验证，未逐一启动枚举到的 42 个应用**

## 结果

| 项 | 结果 | 结论 |
|---|---|---|
| 枚举 | 197 条开始菜单项中 **42 条** UWP/MSIX（ChatGPT/Copilot/Dev Home/Dolby/计算器等） | ✅ 可靠，毫秒~秒级 |
| AUMID 定位 | 计算器 → `Microsoft.WindowsCalculator_8wekyb3d8bbwe!App` | ✅ 名称可查 |
| 启动 | `shell:appsFolder\<AUMID>` → rc=0，**3 秒内 CalculatorApp 进程出现（1 个）** | ✅ 可靠 |
| 收尾 | `Stop-Process` 干净关闭（0 残留） | ✅ |
| 图标 | InstallLocation/manifest 可读（拿到 Square44x44Logo 声明），**但 WindowsApps 目录 ACL 拒绝普通进程读取 logo PNG 本体** | ⚠️ 降级：字母占位图标（与 folder/url/doc 同待遇） |

## 限制（如实声明）

- 仅实测 1 个应用（计算器）。**未验证**：禁止外部唤起的应用、需要登录态的应用、企业 MSIX。
- 旧版 Windows 10 的 Get-StartApps 行为未测（本机 Win11）。
- UWP 无独立 exe 路径：不能重定位（失效=应用被卸载，只能移除登记重新挑）。
- 停止：UWP 进程模型与普通 exe 不同，插件"停止"按钮对 store 类型不保证有效（如实呈现，不假装可控）。

## 实施结论与落地（已按本报告编码）

**结论：支持开发，限定支持范围**（不宣称兼容所有商店应用）。落地内容（v0.3.0）：

- `GET /api/plugins/storeapps`：Get-StartApps 枚举（AppID 含 `!`，去重排序）
- probe 支持 `shell:appsfolder\<AUMID>` 输入（name_hint 带显示名）→ kind=store 清单（aumid 字段，阶段 A schema 已预留）
- 启动：`os.startfile(target)`（与实测同路径）；图标字母占位
- UI：向导第 1 步「🛍 从商店应用挑选」→ 过滤列表 → 点选回向导自动识别
- store 类型在向导中隐藏无意义的窗口模式/提权/一次性选项（由 Windows 决定）

## Schema / API 变更

- `plugin.json`：`kind=store` + `launch.aumid`（**阶段 A 已预留字段，零 schema 变更**）；validate 新增"aumid 必须含 !"校验
- MVP_KINDS 开放 store（六类全部可导入）

## 测试覆盖

单测 6 项（probe 正常/非法 AUMID/校验/启动走 startfile 仿真/真机枚举结构/HTTP 端点）；真机启动证据=本报告第 3 节（产品内启动不重复弹窗测试）。
