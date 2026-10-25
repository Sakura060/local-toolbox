@echo off
rem LANDrop 接收模式：手机浏览器打开终端显示的链接即可上传文件到电脑
cd /d "%~dp0"
python cli.py receive --out "%USERPROFILE%\LANDrop接收"
pause
