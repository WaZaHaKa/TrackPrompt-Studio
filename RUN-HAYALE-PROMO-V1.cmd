@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-hayale-promo-v1.ps1" -Render
if errorlevel 1 (
  echo Hayale build did not complete. Review the error above. Prior files were preserved.
  pause
  exit /b 1
)
echo Hayale package created. Review its verification report and import the timeline into Resolve.
pause
