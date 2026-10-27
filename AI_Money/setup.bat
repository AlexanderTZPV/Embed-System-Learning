@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto directory_failed

echo THS Watch - Windows setup
if not exist "main.py" goto missing_project
if not exist "requirements.txt" goto missing_project
if exist ".venv\Scripts\python.exe" goto install_dependencies

rem Probe before creating the venv. A failed venv creation must not be ignored.
py -3.11 -c "import sys; sys.exit(0 if sys.maxsize > 2**32 else 1)" >nul 2>&1
if not errorlevel 1 goto create_py311
py -3.12 -c "import sys; sys.exit(0 if sys.maxsize > 2**32 else 1)" >nul 2>&1
if not errorlevel 1 goto create_py312
python -c "import sys; sys.exit(0 if sys.version_info[:2] in [(3,11),(3,12)] and sys.maxsize > 2**32 else 1)" >nul 2>&1
if not errorlevel 1 goto create_python

echo ERROR: Python 3.11 or 3.12, 64-bit, was not found.
echo Install it from https://www.python.org/downloads/windows/
echo Select Python Launcher and Add python.exe to PATH during installation.
echo Then close this window and double-click setup.bat again.
goto failed

:create_py311
py -3.11 -m venv ".venv"
if errorlevel 1 goto venv_failed
goto install_dependencies

:create_py312
py -3.12 -m venv ".venv"
if errorlevel 1 goto venv_failed
goto install_dependencies

:create_python
python -m venv ".venv"
if errorlevel 1 goto venv_failed
goto install_dependencies

:install_dependencies
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] in [(3,11),(3,12)] and sys.maxsize > 2**32 else 1)"
if errorlevel 1 goto invalid_venv
".venv\Scripts\python.exe" -m pip --version
if errorlevel 1 goto missing_pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements.txt"
if errorlevel 1 goto install_failed

echo.
echo Setup completed. Double-click demo.bat to try simulated data.
echo For live monitoring, run run.bat and calibrate the screen regions first.
call :pause_if_needed
exit /b 0

:directory_failed
echo ERROR: Could not open the project directory.
goto failed

:missing_project
echo ERROR: main.py or requirements.txt is missing.
echo Extract the WHOLE ZIP to one directory, then run setup.bat there.
goto failed

:venv_failed
echo ERROR: Python failed to create the virtual environment.
echo Check the error above, disk space, and write permissions for this folder.
goto failed

:invalid_venv
echo ERROR: The existing .venv is broken or uses an unsupported Python.
echo Close the app, rename .venv to .venv-backup, then run setup.bat again.
echo Do not rename or delete runtime; it contains your settings and logs.
goto failed

:missing_pip
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto venv_failed
".venv\Scripts\python.exe" -m pip --version
if errorlevel 1 goto venv_failed
goto install_dependencies

:install_failed
echo ERROR: Dependency installation failed. Read the pip error above.
echo Check Internet access and the Python version, then rerun setup.bat.
goto failed

:failed
call :pause_if_needed
exit /b 1

:pause_if_needed
if not "%THS_NO_PAUSE%"=="1" pause
exit /b 0
