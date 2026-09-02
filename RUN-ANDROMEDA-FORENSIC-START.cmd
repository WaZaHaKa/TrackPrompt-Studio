@echo off
setlocal
cd /d "%~dp0"
where pwsh.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  set "PSHOST=pwsh.exe"
) else (
  set "PSHOST=powershell.exe"
)
echo This command remains fail-closed. It starts only when the forensic report proves the exact release is coherent, unheld, human-closed, under the 24-hour P90 limit, and matched to the private audio/cue files.
echo.
%PSHOST% -NoProfile -ExecutionPolicy Bypass -File ".\tools\Invoke-AndromedaForensicOrchestrator.ps1" -Mode Start -OutputMatrix HorizontalOnly
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" echo Start helper exited with code %EXITCODE%.
pause
exit /b %EXITCODE%
