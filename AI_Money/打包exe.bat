@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist .venv\Scripts\python.exe (
  echo 请先运行“安装.bat”。
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name THSWatch --collect-all rapidocr_onnxruntime --collect-all onnxruntime --hidden-import keyring.backends.Windows main.py
if errorlevel 1 exit /b 1
echo 输出在 dist\THSWatch。分发时请复制整个文件夹，不要只复制exe。
pause
