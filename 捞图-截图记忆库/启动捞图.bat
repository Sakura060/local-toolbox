@echo off
rem Laotu - screenshot memory search (port 8637)
cd /d "%~dp0"
echo Starting Laotu at http://127.0.0.1:8637 ... (first launch loads OCR model)
python cli.py start %*
echo.
echo Server exited.
pause
