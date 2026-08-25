@echo off
REM S3文件同步工具 - Windows打包脚本

echo ====================================
echo S3文件同步工具 - 打包脚本
echo ====================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未检测到Python
    pause
    exit /b 1
)

REM 检查PyInstaller是否安装
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo 正在安装PyInstaller...
    pip install pyinstaller
    if errorlevel 1 (
        echo 错误: PyInstaller安装失败
        pause
        exit /b 1
    )
)

echo.
echo 开始打包...
echo.

REM 清理旧的打包文件
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"

REM 执行打包
pyinstaller build.spec

if errorlevel 1 (
    echo.
    echo 错误: 打包失败
    pause
    exit /b 1
)

echo.
echo ====================================
echo 打包完成!
echo 可执行文件位于: dist\S3FileSync.exe
echo ====================================
echo.

pause
