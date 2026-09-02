@echo off
setlocal
cd /d "%~dp0"
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Collect-AndromedaReleaseSourceKit.ps1" -OpenOutputFolder
if errorlevel 1 (
  echo.
  echo Collection failed. Review the error above.
  pause
  exit /b 1
)
echo.
pause
