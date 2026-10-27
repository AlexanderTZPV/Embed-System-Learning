@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0setup.bat" %*
exit /b %errorlevel%
