@echo off
rem NoteNest 本地笔记 启动脚本（笔记默认存放在 %USERPROFILE%\NoteNest）
cd /d "%~dp0"
start "" http://127.0.0.1:8890/
python cli.py --port 8890
