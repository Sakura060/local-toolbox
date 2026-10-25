@echo off
rem ClipVault - clipboard timeline (port 8643)
cd /d "%~dp0"
echo Starting ClipVault at http://127.0.0.1:8643 ...
python cli.py serve %*
echo.
echo Server exited.
pause
