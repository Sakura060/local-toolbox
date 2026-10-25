@echo off
rem FileFlow Web UI 启动脚本
cd /d "%~dp0"
start "" http://127.0.0.1:8635/
python -m fileflow.webui 8635
