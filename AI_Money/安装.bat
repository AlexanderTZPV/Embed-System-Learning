@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if exist .venv\Scripts\python.exe goto dependencies
py -3.11 -m venv .venv
if errorlevel 1 py -3.12 -m venv .venv
if not exist .venv\Scripts\python.exe (
  echo 请先安装 Windows x64 Python 3.11 或 3.12，包含 Python Launcher。
  pause
  exit /b 1
)
:dependencies
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo 安装失败，请保留错误信息并检查网络。
  pause
  exit /b 1
)
echo 安装完成。双击“启动.bat”；首次请进入设置完成区域标定。
pause
