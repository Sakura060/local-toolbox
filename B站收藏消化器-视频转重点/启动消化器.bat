@echo off
rem BiliDigest - video to key points (port 8639)
cd /d "%~dp0"
echo Starting BiliDigest at http://127.0.0.1:8639 ... (first ASR run downloads model)
python cli.py serve %*
echo.
echo Server exited.
pause
