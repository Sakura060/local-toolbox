@echo off
rem Inspector - pre-send file privacy check (port 8641)
cd /d "%~dp0"
echo Starting Inspector at http://127.0.0.1:8641 ...
python cli.py serve %*
echo.
echo Server exited.
pause
