@echo off
REM Same app, but keeps the console open so errors stay visible.
cd /d "%~dp0"
py -3 "dvr_viewer.py"
echo.
echo ---- app exited, press a key ----
pause >nul
