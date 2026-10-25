@echo off
rem ToolHub tray resident (windowless, pythonw)
cd /d "%~dp0"
start "ToolHub Tray" pythonw cli.py tray
echo ToolHub tray started. Open http://127.0.0.1:8642
timeout /t 2 >nul
