@echo off
rem 运行 FileFlow 全部测试
cd /d "%~dp0"
python -m unittest discover -s tests
pause
