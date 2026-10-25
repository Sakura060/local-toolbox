@echo off
rem ToolHub Desktop - native window shell (port 8650)
cd /d "%~dp0"
echo Starting ToolHub Desktop at http://127.0.0.1:8650 ...
python cli.py desktop %*
echo.
echo Desktop exited.
pause
