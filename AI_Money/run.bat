@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto missing_environment
if not exist ".venv\Scripts\python.exe" goto missing_environment
if not exist "main.py" goto missing_project
".venv\Scripts\python.exe" "main.py" %*
set "THS_EXIT_CODE=%errorlevel%"
if "%THS_EXIT_CODE%"=="0" exit /b 0
echo.
echo The app exited with an error. Keep the error text above for diagnosis.
if not "%THS_NO_PAUSE%"=="1" pause
exit /b %THS_EXIT_CODE%

:missing_environment
echo ERROR: The Python environment is missing. Run setup.bat first.
if not "%THS_NO_PAUSE%"=="1" pause
exit /b 1

:missing_project
echo ERROR: main.py is missing. Extract the WHOLE project ZIP.
if not "%THS_NO_PAUSE%"=="1" pause
exit /b 1
