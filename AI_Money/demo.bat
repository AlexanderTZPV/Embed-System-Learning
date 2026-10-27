@echo off
setlocal EnableExtensions DisableDelayedExpansion
call "%~dp0run.bat" --demo %*
exit /b %errorlevel%
