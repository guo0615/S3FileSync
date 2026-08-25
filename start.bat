@echo off
REM S3文件同步工具 - Windows启动脚本

echo ====================================
echo S3文件同步工具
echo ====================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未检测到Python，请先安装Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 检查依赖是否安装
echo 检查依赖...
pip show PyQt6 >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo 错误: 依赖安装失败
        pause
        exit /b 1
    )
)

echo.
echo 启动应用...
echo.

REM 运行应用
python main.py

if errorlevel 1 (
    echo.
    echo 错误: 应用运行失败
    pause
)
