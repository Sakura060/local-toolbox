# 文件户口本 — 文件身世记录

给每个文件记"身世"：从哪来（哪个网址/谁发的）、被改名/移动过几次——右键查户口。
全部只读采集，绝不写注册表、不装驱动、不碰聊天软件协议。

## 快速开始

双击 `启动户口本.bat`（或 `python cli.py serve`），浏览器打开 http://127.0.0.1:8638

1. 点右上角**"盘点下载夹"**——首次建库，读取来源并归因
2. 搜索框输入文件名关键词或完整路径 → 每个文件一张"身世卡"

来源标记：**★ 实证**（文件自带来源证书 ADS）　**☆ 下载记录**（浏览器历史库）　**？推测**（接收目录启发式）

## CLI

```
python cli.py scan [<目录>...] [--no-browser]  # 盘点目录并归因来源（默认下载夹）
python cli.py query <文件路径>                  # 查一个文件的身世（含谱系链）
python cli.py search <关键词>                   # 按文件名搜来源库
python cli.py watch [--volumes C D] [--interval 2]  # 持续记账（需管理员，右键"以管理员身份运行"）
python cli.py deep [--volumes C D] [--history]  # 全量 MFT 快照 + 日志历史回溯（需管理员）
python cli.py serve [--port 8638]               # Web 查询界面
python cli.py stats
```

## 来源归因优先级

| 级别 | 通道 | 说明 |
|---|---|---|
| ★ ads | NTFS Zone.Identifier | 浏览器/应用下载的文件自带，含 HostUrl/ReferrerUrl |
| ☆ browser | Chrome/Edge downloads 库 | 只读拷贝副本再查（防锁），可回溯数月 |
| ？ dir | 接收目录启发式 | 路径含 xwechat_files / Tencent Files / Downloads 等，标注"推测" |

## 事件时间线（watch 模式）

USN Journal 是 NTFS 原生日志，`watch` 以 2s 间隔增量读取"新建/删除/改名"事件入库。
**从启动之日起记录**。

## 谱系链（v0.2 deep 模式）

`python cli.py deep --history`（管理员）一次完成两件事：

1. **全量 MFT 快照**：FSCTL_ENUM_USN_DATA 只读枚举整卷文件（frn/父frn/名字）入 `files` 表，
   C 盘百万文件约几十秒；查询时沿 pfrn 链重建任意文件的完整路径，无需整卷遍历目录。
2. **历史回溯**：从日志最早可用点（lowest_valid_usn）读到日志头，rename 新旧名共享同一
   frn、pfrn 变化即移动 → 按文件重建"新建 → 改名 → 移动 → 删除"谱系链。

之后 `query` 与 Web 身世卡（输入完整路径时顶部）会显示谱系链。日志范围有限（通常数周），
超出范围的历史 NTFS 不保留，诚实标注。

## 测试

```
python -m unittest discover -s tests   # 46 项：USN 解析 / ADS / 浏览器库 / 归因 / MFT 路径重建 / 谱系链 / API
```

真实数据冒烟（2026-09-26）：下载夹 179 文件 100% 归因，42 个带真实来源网址。

## 隐私与安全红线

- 对文件系统/浏览器库**绝对只读**；浏览器 History 先复制临时副本再打开
- USN 只调只读控制码（ENUM/READ/GET），绝不创建/删除日志
- 全部数据存本地 `data/hukou.db`，软件零联网
