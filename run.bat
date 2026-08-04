@echo off
REM Workbench 启动脚本(Windows)
REM
REM 用法:
REM   双击本文件            默认端口 8000
REM   run.bat --port 9000   指定端口
REM   run.bat --data D:\科研数据   指定数据文件夹
REM
REM 真正的逻辑全在 start.py 里,这里只负责找一个可用的 Python 把它拉起来。

cd /d "%~dp0"

REM Windows 上 python.org 的安装包会注册 py 启动器,优先用它;
REM 没有就退回 python。版本够不够由 start.py 自己判断并给出中文提示。
where py >nul 2>&1
if %errorlevel%==0 (
    py start.py %*
    goto :end
)

where python >nul 2>&1
if %errorlevel%==0 (
    python start.py %*
    goto :end
)

echo.
echo   [X] 没有找到 Python
echo.
echo       请先安装 Python 3.10 或更高版本:
echo           https://www.python.org/downloads/
echo.
echo       安装时请务必勾选 "Add Python to PATH"
echo.

:end
REM 双击运行时,窗口不要立刻关闭,否则看不到任何信息
pause
