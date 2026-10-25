@echo off
rem LANDrop 共享模式：把指定文件夹共享给局域网下载（手机浏览器打开链接即可下载）
cd /d "%~dp0"
set /p SHAREDIR=要共享的文件夹路径（回车=共享当前目录旁的示例）:
if "%SHAREDIR%"=="" set SHAREDIR=%~dp0..
echo.
python cli.py share "%SHAREDIR%"
pause
