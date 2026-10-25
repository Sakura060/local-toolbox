# FileFlow 智能文件整理器

按规则自动整理杂乱目录（下载文件夹、桌面等），**先预览、再执行、可撤销**。纯 Python 标准库实现，零第三方依赖。

## 功能

- **规则整理**：按扩展名 / 修改日期 / 文件名模式 / 文件大小归类，内置 8 组常用规则（图片、文档、音频、视频、压缩包、安装包、代码、字体）
- **预览优先**：任何操作先产出整理计划，确认无误才动文件
- **一键撤销**：每次执行生成操作日志（JSON），`undo` 按逆序完整回滚；原位置被占用时自动改名为 `(restored-N)` 保留双方
- **冲突消解**：目标目录有同名文件时自动追加 `(1) (2)` 序号，绝不覆盖
- **重复文件清理**：大小 → 快速哈希 → 全量哈希三级检测，把多余副本移入回收目录（不直接删除），可释放空间一目了然
- **双界面**：CLI 与本地 Web UI（仅监听 127.0.0.1）

## 快速开始

```bat
:: 扫描概览
python cli.py scan "C:\Users\你\Downloads"

:: 预览整理计划（不改动任何文件）
python cli.py plan "C:\Users\你\Downloads"

:: 执行整理（去掉 -y 会先请求确认）
python cli.py run "C:\Users\你\Downloads" -y

:: 反悔了？一键撤销
python cli.py undo

:: 查找重复文件 / 清理
python cli.py dup "C:\Users\你\Downloads"
python cli.py dup "C:\Users\你\Downloads" --clean
```

Web UI：

```bat
python -m fileflow.webui        # 打开 http://127.0.0.1:8635
```

**监控模式**（下载目录全自动整理）：

```bat
:: 每 10 秒扫描一次，文件静置 5 秒（防整理写入中的下载）后自动归类
python cli.py watch "C:\Users\你\Downloads"

:: 自定义节奏；同样支持 --preset / -r 规则
python cli.py watch "D:\采集" --interval 3 --min-age 2 --preset designer
```

每次自动整理都是独立操作日志，Ctrl+C 停止后会列出最近日志，`undo` 可按次撤销。

Web UI 里也有**「自动监控」卡片**：填入目录 → 开始监控 → 实时查看事件流 → 停止监控（服务端后台线程执行，页面轮询刷新，刷新页面自动恢复状态）。

## 自定义规则

编辑 `~/.fileflow/config.json`：

```json
{
  "use_defaults": true,
  "preset": null,
  "rules": [
    {"name": "发票", "type": "name_pattern", "pattern": "*发票*", "target": "财务/发票"},
    {"name": "按月归档", "type": "date", "format": "%Y-%m", "target": "归档/{date}"},
    {"name": "大文件", "type": "size", "min_mb": 100, "target": "大文件"},
    {"name": "设计素材", "type": "extension", "pattern": "psd ai sketch", "target": "设计"}
  ]
}
```

规则类型：`extension`（空格分隔扩展名）、`date`（`{date}` 被 `format` 结果替换）、`name_pattern`（fnmatch 通配符）、`size`（`min_mb`/`max_mb` 区间）。用户规则优先于内置规则，先命中先生效。

**预设模板**（替代内置默认规则）：

```bat
python cli.py run "D:\下载" --preset developer   :: 代码/配置/文档/压缩包…优先
python cli.py run "D:\素材" --preset designer    :: 设计源文件/位图/矢量/字体…
python cli.py run "D:\学习" --preset student     :: 课件/表格/课程视频…
python cli.py run "D:\财务" --preset finance     :: 发票/报销/对账单 + 按月归档
```

也可在 config.json 里设置 `"preset": "developer"` 持久生效；`--no-defaults` 则只用自定义规则。

也可以把规则写在独立 JSON 文件里临时使用：`python cli.py run <目录> -r 我的规则.json`。

## 安全设计

- 所有移动目标强制位于整理根目录内（执行前逐条校验，防规则配置越界）
- 撤销时校验日志中的原路径仍在原根目录内（防篡改日志指路）
- 操作日志文件名只含白名单字符，临时文件 + 原子替换写入
- Web UI 只绑定 `127.0.0.1`；页面渲染用 `textContent`，不受文件名注入影响
- 重复文件清理只移入 `.fileflow-trash` 回收目录，确认后手动删除

## 测试

```bat
python -m unittest discover -s tests
```

覆盖：扫描（隐藏目录/深度限制/中文文件名）、规则解析与匹配、计划生成（冲突消解/自锁防护/纯预览）、执行与撤销（往返回滚/占用处理/越界拒绝/篡改日志防护/单条失败不中断）、重复检测（跨目录/排除目录/回收计划）、Web API 全链路（含 400/404 分支）。

## 目录结构

```
fileflow/
  scanner.py    目录扫描
  rules.py      规则引擎（含预设模板）
  planner.py    计划生成（dry-run 核心）
  executor.py   执行 / 日志 / 撤销
  dedup.py      重复文件检测
  watcher.py    watch 监控模式
  config.py     配置管理（~/.fileflow/config.json）
  webui.py      本地 Web UI（含 watch 后台线程管理）
  page.html     Web 前端单页
cli.py          命令行入口
tests/          单元 + 集成测试
```

## 版本

- v0.1 核心引擎 + CLI + 撤销（34 测试）
- v0.2 本地 Web UI（+7 测试）
- v0.3 重复文件检测与清理（+9 测试）
- v0.4 打磨：安全加固、README、运行脚本
- v0.5 预设规则模板（developer/designer/student/finance，CLI --preset 与 config 双入口）（+8 测试，共 59）
- v0.6 **watch 监控模式**：定时自动整理新文件，min-age 保护写入中的下载（+6 测试，共 65）；真机验证中途放入的文件自动归类
- v0.7 Web UI **自动监控卡片**：watch 后台线程管理（WatchController）+ 实时事件流轮询 + 页面状态恢复（+5 测试，共 70）；浏览器端到端验收（启动→自动归类→事件显示→停止）
