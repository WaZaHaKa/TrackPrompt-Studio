@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-hayale-promo-v2.ps1" -Create
if errorlevel 1 (
  echo V2 creation stopped. Read the error above; do not erase saved attempts to retry.
  pause
  exit /b 1
)
echo V2 reference promo and Resolve package are ready for your visual review.
pause
