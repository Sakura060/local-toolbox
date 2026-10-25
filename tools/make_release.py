"""构建 GitHub 开源发布树：白名单源码 + 对外 README + 隐私断言（失败即拒绝发布）。

产物：
  D:\\项目大全\\发布包-GitHub\\repo\\        可直接 push 的源码树（已脱敏）
  D:\\项目大全\\发布包-GitHub.zip           repo 的压缩包（人工审查用）

2026-10-25 重写：黑名单 → 白名单（旧版把 MV 工程 11236 文件、便携包 3000+ 文件
卷进发布树，被隐私断言拦下）。开源范围 = 10 内置工具（读 desktop.cfg.BUILTINS
自动跟随）+ ToolHub-工具箱门户 + localcore + apps + 工作区共享 tools。
内部状态文档（PROJECT_STATE/DEV_LOG/TODO/DECISIONS/BLUEPRINT）不进开源树。

历史授权记录（沿用）：GitHub 用户 Sakura060，仓库名 local-toolbox，公开，MIT；
用户机器 github.com 直连不通、api/ssh.github.com:443 可达（推送通道见 DEV_LOG）。
"""

from __future__ import annotations

import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "发布包-GitHub"
REPO = OUT / "repo"

EXCLUDE_DIR_NAMES = {
    "data", "__pycache__", ".git", "node_modules", ".venv", "venv",
    ".mimosa", ".v2c", ".video_agent", ".zcode", ".idea", ".vscode",
    "edge-profile", "ocr_batches",
}
EXCLUDE_FILE_SUFFIXES = (".pyc", ".pyo", ".log", ".db", ".sqlite3")
EXCLUDE_FILE_NAMES = {"tests_last_probe.py", "_tmp_page.js",
                      "uwp_feasibility_raw.txt", "_probe_cred.py"}

GITIGNORE = """# 运行时个人数据（绝不入库）
data/
*.db
*.sqlite3
*.log

# Python
__pycache__/
*.pyc
.venv/
venv/

# IDE / 会话
.idea/
.vscode/
.zcode/
"""

LICENSE = """MIT License

Copyright (c) 2026 Sakura060

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

README = """# ToolHub — 本地工具箱 | Local-first Windows Toolbox

**一个窗口装下一整套本地工具，还能把电脑上任意软件登记进来当插件——数据全部留在本机。**

## 中文说明

ToolHub 桌面版 v{version}：10 个纯 Python 本地工具 + 统一桌面入口（原生窗口），
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
python tools\\run_all_tests.py     # 全仓库 11 个项目、657 个测试（约 2 分钟）
```

### 目录结构

- `ToolHub-工具箱门户/` —— 桌面版 + 旧网页门户（desktop/ 包为核心：四层注册表、
  插件系统、工作场景、环境自检；docs/ 里有架构与 API 对接文档）
- 各工具目录 / `localcore/`（共享件）/ `apps/`（第三方示范插件）
- `tools/` —— 构建/测试辅助脚本（打包、全量测试、发布树生成）

## English

ToolHub Desktop v{version}: 10 local-first Python tools behind one native
Windows window, plus a plugin system that registers any local exe / shortcut /
script / folder / URL / Microsoft Store app as a launchable card — with
command-line argument templates, bulk directory scan import, shareable plugin
manifests, and work scenes (launch a whole group in one click).

**No telemetry, no accounts, no cloud sync.** All HTTP services bind to
127.0.0.1 only. Architecture and API docs live in
`ToolHub-工具箱门户/docs/` (Chinese; the code and tests are the source of truth).

## License

MIT — see [LICENSE](LICENSE).
"""


def clean_text(text: str) -> str:
    """脱敏：个人绝对路径 → 可移植写法。"""
    return (text
            .replace(r"%USERPROFILE%\NoteNest", r"%USERPROFILE%\NoteNest")
            .replace(r"%USERPROFILE%", r"%USERPROFILE%")
            .replace("%USERPROFILE%", "%USERPROFILE%"))


def read_version() -> str:
    init = (ROOT / "ToolHub-工具箱门户" / "desktop" / "__init__.py").read_text(
        encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    return m.group(1) if m else "?"


def copy_tree(src: Path, dst: Path) -> tuple[int, int]:
    n_files = n_skip = 0
    for item in src.iterdir():
        if item.name in EXCLUDE_DIR_NAMES and item.is_dir():
            n_skip += 1
            continue
        if item.name in EXCLUDE_FILE_NAMES or item.suffix in EXCLUDE_FILE_SUFFIXES:
            n_skip += 1
            continue
        if item.is_dir():
            sub_files, sub_skip = copy_tree(item, dst / item.name)
            n_files += sub_files
            n_skip += sub_skip
        else:
            dst.mkdir(parents=True, exist_ok=True)
            if item.suffix in (".md", ".py", ".txt", ".html", ".bat", ".json",
                               ".cfg", ".toml", ".yml", ".yaml", ".js", ".vbs"):
                (dst / item.name).write_text(
                    clean_text(item.read_text(encoding="utf-8", errors="replace")),
                    encoding="utf-8")
            else:
                shutil.copy2(item, dst / item.name)
            n_files += 1
    return n_files, n_skip


def main() -> int:
    # 白名单：内置工具目录自动读注册表（与 tools/build_portable.py 同哲学）
    sys.path.insert(0, str(ROOT / "ToolHub-工具箱门户"))
    from desktop.cfg import BUILTINS  # noqa: E402
    include_dirs = {"ToolHub-工具箱门户", "localcore", "tools", "apps"}
    include_dirs.update(t.launch_dir for t in BUILTINS if t.launch_dir)

    if OUT.exists():
        shutil.rmtree(OUT)
    REPO.mkdir(parents=True)

    total = 0
    for name in sorted(include_dirs):
        d = ROOT / name
        if not d.is_dir():
            print(f"  skip (missing) {name}")
            continue
        n, _ = copy_tree(d, REPO / name)
        total += n
        print(f"  dir  {name}: {n} files")

    (REPO / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (REPO / "LICENSE").write_text(LICENSE, encoding="utf-8")
    (REPO / "README.md").write_text(README.format(version=read_version()),
                                    encoding="utf-8")
    print("  file .gitignore + LICENSE + README.md")
    total += 3

    # ---- 隐私断言：任何一项命中即拒绝发布 ----
    # 说明：断言只认"真实身份特征"——用户名 33754、真实微信安装绝对路径、本工作区绝对路径。
    # "微信"/"xwechat_files" 等裸词是产品功能（微信接收目录识别）与公开目录结构名，不算泄露；
    # C:\Users\t、C:\Users\zhangsan 等是测试夹具虚构路径，放行。
    leaks = []
    for p in REPO.rglob("*"):
        if not p.is_file() or p.suffix not in (".md", ".py", ".txt", ".html",
                                               ".bat", ".json", ".cfg", ".toml",
                                               ".yml", ".yaml", ".js", ".vbs"):
            continue
        rel = str(p.relative_to(REPO))
        if rel == r"tools\make_release.py":
            continue          # 断言词所在文件自身
        text = p.read_text(encoding="utf-8", errors="replace")
        if "33754" in text:
            leaks.append(rel + " (username)")
        if "D:\\项目大全" in text or "D:/项目大全" in text:
            leaks.append(rel + " (workspace abs path)")
        if "D:\\微信聊天" in text or r"D:\微信聊天" in text:
            leaks.append(rel + " (wechat install path)")
        if "C:\\Users\\33754" in text:
            leaks.append(rel + " (users path)")
    data_dirs = [str(p.relative_to(REPO)) for p in REPO.rglob("data") if p.is_dir()]
    dbs = [str(p.relative_to(REPO)) for p in REPO.rglob("*")
           if p.suffix in (".db", ".sqlite3")]
    big = [(str(p.relative_to(REPO)), p.stat().st_size) for p in REPO.rglob("*")
           if p.is_file() and p.stat().st_size > 5 * 1024 * 1024]

    if leaks or data_dirs or dbs or big:
        print("PRIVACY ASSERTION FAILED:")
        print("  leaks:", leaks[:40])
        print("  data dirs:", data_dirs)
        print("  db files:", dbs)
        print("  big files (>5MB):", big)
        return 1

    # ---- zip（人工审查用） ----
    zip_path = ROOT / "发布包-GitHub.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(REPO.rglob("*")):
            if p.is_file():
                z.write(p, Path("repo") / p.relative_to(REPO))

    print(f"\nOK files={total} -> {REPO}")
    print(f"OK zip -> {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
