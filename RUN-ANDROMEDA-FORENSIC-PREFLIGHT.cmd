@echo off
setlocal
cd /d "%~dp0"
where pwsh.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  set "PSHOST=pwsh.exe"
) else (
  set "PSHOST=powershell.exe"
)
%PSHOST% -NoProfile -ExecutionPolicy Bypass -File ".\tools\Invoke-AndromedaForensicOrchestrator.ps1" -Mode Preflight -OpenReportFolder
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" echo Preflight/audit exited with code %EXITCODE%.
pause
exit /b %EXITCODE%
