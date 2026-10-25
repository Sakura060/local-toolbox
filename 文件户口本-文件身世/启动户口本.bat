@echo off
rem Hukou - file origin tracker (port 8638)
cd /d "%~dp0"
echo Starting Hukou at http://127.0.0.1:8638 ...
python cli.py serve %*
echo.
echo Server exited.
pause
