@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0build.bat" %*
exit /b %errorlevel%
