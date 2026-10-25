@echo off
rem DocLine - document lineage and diff (port 8640)
cd /d "%~dp0"
echo Starting DocLine at http://127.0.0.1:8640 ...
python cli.py serve %*
echo.
echo Server exited.
pause
