# B站收藏消化器 — 视频转文字重点

把收藏的视频变成能搜的文字：本地听写 → **章节 / 关键词 / 重点句** 三件套 → 带时间戳点击跳转的网页。
**全流程零 API token**——ASR 用本地 faster-whisper（CPU int8），"重点"用纯算法抽取，
输出全部来自原文，不编一个字（所以叫"重点提取"不叫"智能摘要"）。

## 快速开始

双击 `启动消化器.bat`（或 `python cli.py serve`），浏览器打开 http://127.0.0.1:8639

1. 粘贴 **B站视频链接** 或 **本地音视频文件路径** → 点"消化它"
2. 任务列表看进度（下载 → 本地转写 xx/xx s → 提取重点）
3. 完成后点开：播放器 + 章节（跳着看）+ 关键词 + 重点句（点击跳转）+ 完整文字稿

## CLI

```
python cli.py add <URL 或 文件路径> [--model tiny|base|small]  # 消化一个（前台跑完）
python cli.py run                    # 处理所有待处理任务
python cli.py serve [--port 8639]    # Web 界面（自动恢复未完成任务）
python cli.py list / stats
```

## 成本与档位

| 项 | 说明 |
|---|---|
| API 费用 | **永远是 0**（无云端调用） |
| 模型 | tiny 75MB / base 150MB（默认）/ small 500MB，首次联网下载一次，之后完全离线 |
| 速度（CPU） | 约 2~4 倍实时：20 分钟视频 ≈ 6~10 分钟（base/tiny 档更快） |
| 显卡 | 不需要 |

## 支持的输入

- 本地：mp4 / mkv / flv / webm / mov / avi / mp3 / m4a / aac / wav / flac / ogg / opus
- B站：单视频链接（yt-dlp 只拉音频轨、单并发、带 UA）

失败提示都是人话：需要登录/地区限制的视频 v0.1 不支持 cookies；yt-dlp 未安装会给安装命令。

## 测试

```
python -m unittest discover -s tests   # 31 项：重点提取全部纯函数 + DB + fetch + server（含 Range 206）
```

## 隐私

转写在本地完成，文字稿存 `data/transcripts/*.txt`，不上传任何内容。
