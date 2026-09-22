@echo off
setlocal
cd /d "%~dp0"
echo Hayale Flowers V2.1 - safely continue existing run 344ab3d2444c
echo Completed shots are preserved. An existing execution owner blocks a second worker.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0continue-hayale-v21.ps1"
if errorlevel 1 (
  echo.
  echo V2.1 stopped. Read the message above. Do not delete saved attempts to retry.
)
pause
