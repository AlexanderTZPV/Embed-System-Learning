@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist .venv\Scripts\python.exe (
  echo 请先运行“安装.bat”。
  pause
  exit /b 1
)
.venv\Scripts\python.exe main.py %*
if errorlevel 1 pause
