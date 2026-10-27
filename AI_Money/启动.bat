@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0run.bat" %*
exit /b %errorlevel%
