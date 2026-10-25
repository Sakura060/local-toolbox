# 捞图 — 截图视觉记忆库

给电脑里的截图/图片装一个搜索引擎：图里的文字能搜、文件名能搜，全程本地、零 API、断网可用。

## 快速开始

双击 `启动捞图.bat`（或 `python cli.py start`），浏览器打开 http://127.0.0.1:8637

- 首次启动自动索引你的截图目录（图片\Screenshots），进度条在页面顶部
- 搜索框输入截图里出现过的文字（如"快递单号""优惠券"），半秒出图
- 点击卡片用系统看图器打开原图；右上角"＋ 目录"可添加任意文件夹
- **时间线**：按天柱状图看截图密度，点柱子只看当天
- **查重**：一键找相似/重复截图（dHash 感知哈希，保留最新一张）
- **来源标注**：落在微信/QQ 接收目录下的图片自动标注"微信/QQ"（路径推测）

## CLI

```
python cli.py index <目录> [<目录>...]   # 建索引（增量、可重复执行）
python cli.py start [--port 8637] [--dirs 目录...] [--interval 30]  # Web + 后台定时重扫
python cli.py serve [--port 8637]        # 只起服务不扫描
python cli.py stats                      # 索引统计
```

## 工作原理

- **OCR**：RapidOCR（onnxruntime，模型随包分发，离线可用），进程内懒加载单例 + 线程锁
- **中文检索**：jieba 分词写入 SQLite FTS5（FTS5 原生对中文是整句 token，直用必废），
  查询侧同切分 + 整词短语二级尝试 + LIKE 兜底
- **增量索引**：按 (size, mtime_ns) 指纹跳过未变化文件；删除的文件自动出库；
  OCR 失败的记录下次自动重试
- **缩略图**：Pillow 生成 JPEG 落盘 `data/thumbs/`（HTTP 直发，不占 DB）
- **相似查重（v0.2）**：索引时算 64 位 dHash（9×8 灰度相邻比较，纯 Pillow 无 freetype）；
  查重用并查集分组（汉明距 ≤8 视为相似），保留每组最新一张。dHash 存 SQLite 需
  有符号转换（SQLite INTEGER 上限 2^63-1）；NULL 表示未计算——0 是纯色图的合法哈希，不能当哨兵
- **时间线（v0.2）**：mtime 按天/小时 GROUP BY 聚簇；`/api/search?t0=&t1=` 时间范围过滤

## 测试

```
python -m unittest discover -s tests     # 59 项（引擎无关部分）
python tools/make_test_images.py         # 生成合成测试图（freetype 只许在这个进程）
python tools/ocr_smoke.py                # 真引擎往返 + 索引链路冒烟
python tools/ui_preview.py               # 种子库 UI 预览（8649 端口，查重/时间线演示）
```

## 已知环境约束（重要）

本开发机上实测：**PIL freetype（画字）与 RapidOCR 推理同进程互斥**（无论先后顺序），
且无控制台（detached）环境下推理会挂起。因此：

1. 产品代码**零 freetype**（缩略图只做缩放），真实使用（控制台启动）无此问题；
2. 排查 OCR 卡点看 `data/ocr_trace.log`（每个阶段有轨迹）；
3. 调试命令必须从真实控制台运行（双击 .bat 或 cmd 里跑）。

## 隐私

全部本地计算：OCR、索引、搜索、缩略图都不联网，不上传任何数据。
