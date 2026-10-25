@echo off
rem ToolHub - unified launcher and dashboard for all local tools (port 8642)
cd /d "%~dp0"
echo Starting ToolHub at http://127.0.0.1:8642 ...
python cli.py serve %*
echo.
echo Server exited.
pause
