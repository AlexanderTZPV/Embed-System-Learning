@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0demo.bat" %*
exit /b %errorlevel%
