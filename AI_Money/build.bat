@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto failed
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Run setup.bat first.
  goto failed
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements-dev.txt"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name THSWatch --collect-all rapidocr_onnxruntime --collect-all onnxruntime --hidden-import keyring.backends.Windows "main.py"
if errorlevel 1 goto failed
echo Build completed: dist\THSWatch\THSWatch.exe
echo Distribute the WHOLE THSWatch folder, not just the exe.
if not "%THS_NO_PAUSE%"=="1" pause
exit /b 0

:failed
echo ERROR: Build stopped. Keep the error text above for diagnosis.
if not "%THS_NO_PAUSE%"=="1" pause
exit /b 1
