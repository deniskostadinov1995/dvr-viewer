@echo off
REM Rebuilds dist\DVR Viewer\DVR Viewer.exe from the .py sources.
REM Your settings in %LOCALAPPDATA%\DVR Viewer are not touched.
cd /d "%~dp0"
py -3 -m PyInstaller --noconfirm --clean "DVR Viewer.spec"
echo.
if exist "dist\DVR Viewer\DVR Viewer.exe" (
  echo Build OK  -^>  dist\DVR Viewer\DVR Viewer.exe
) else (
  echo BUILD FAILED - scroll up for the error.
)
pause
