"""重新生成三个启动 .bat：正文纯 ASCII、无 chcp、CRLF 不敏感（LF 可用）。

背景（2026-09-26 反向检查结论）：Write 工具产出的 .bat 是 LF + UTF-8 中文，
此前版本在 chcp 65001 之后紧跟中文 title/echo 行——cmd 批处理在 chcp 后
重新解码文件，字节偏移错位导致闪退（对照：能正常跑的 FileFlow .bat 无 chcp、
中文只在 rem 注释里）。新规范：
  1. 正文一律 ASCII（提示语用英文）；
  2. 不用 chcp（Python 3.13 真控制台走 WinConsoleIO，中文显示与代码页无关）；
  3. 浏览器由 Python 侧延迟打开（服务就绪后再开，避免白屏）。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BATS = {
    "捞图-截图记忆库\\启动捞图.bat": """@echo off
rem Laotu - screenshot memory search (port 8637)
cd /d "%~dp0"
echo Starting Laotu at http://127.0.0.1:8637 ... (first launch loads OCR model)
python cli.py start %*
echo.
echo Server exited.
pause
""",
    "文件户口本-文件身世\\启动户口本.bat": """@echo off
rem Hukou - file origin tracker (port 8638)
cd /d "%~dp0"
echo Starting Hukou at http://127.0.0.1:8638 ...
python cli.py serve %*
echo.
echo Server exited.
pause
""",
    "B站收藏消化器-视频转重点\\启动消化器.bat": """@echo off
rem BiliDigest - video to key points (port 8639)
cd /d "%~dp0"
echo Starting BiliDigest at http://127.0.0.1:8639 ... (first ASR run downloads model)
python cli.py serve %*
echo.
echo Server exited.
pause
""",
    "外发卫士-发前体检\\启动外发卫士.bat": """@echo off
rem Inspector - pre-send file privacy check (port 8641)
cd /d "%~dp0"
echo Starting Inspector at http://127.0.0.1:8641 ...
python cli.py serve %*
echo.
echo Server exited.
pause
""",
    "文档户口本-文档谱系\\启动文档户口本.bat": """@echo off
rem DocLine - document lineage and diff (port 8640)
cd /d "%~dp0"
echo Starting DocLine at http://127.0.0.1:8640 ...
python cli.py serve %*
echo.
echo Server exited.
pause
""",
    "ToolHub-工具箱门户\\启动工具箱.bat": """@echo off
rem ToolHub - unified launcher and dashboard for all local tools (port 8642)
cd /d "%~dp0"
echo Starting ToolHub at http://127.0.0.1:8642 ...
python cli.py serve %*
echo.
echo Server exited.
pause
""",
    "ToolHub-工具箱门户\\启动托盘.bat": """@echo off
rem ToolHub tray resident (windowless, pythonw)
cd /d "%~dp0"
start "ToolHub Tray" pythonw cli.py tray
echo ToolHub tray started. Open http://127.0.0.1:8642
timeout /t 2 >nul
""",
    "ClipVault-剪贴板时间线\\启动剪贴板.bat": """@echo off
rem ClipVault - clipboard timeline (port 8643)
cd /d "%~dp0"
echo Starting ClipVault at http://127.0.0.1:8643 ...
python cli.py serve %*
echo.
echo Server exited.
pause
""",
    "ToolHub-工具箱门户\\启动桌面版.bat": """@echo off
rem ToolHub Desktop - native window shell (port 8650)
cd /d "%~dp0"
echo Starting ToolHub Desktop at http://127.0.0.1:8650 ...
python cli.py desktop %*
echo.
echo Desktop exited.
pause
""",
}

for rel, body in BATS.items():
    p = ROOT / rel
    p.write_bytes(body.replace("\n", "\r\n").encode("ascii"))
    b = p.read_bytes()
    print(f"OK {p.name}: {len(b)}B ascii={all(c < 128 for c in b)} crlf={b.count(b'\\r\\n') > 3}")
