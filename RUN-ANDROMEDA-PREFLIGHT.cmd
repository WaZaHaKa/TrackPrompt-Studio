@echo off
setlocal
for %%I in ("%~dp0.") do set "PACKAGE_ROOT=%%~fI"
set "SCRIPT=%PACKAGE_ROOT%\tools\Invoke-AndromedaLatestProduction.ps1"
set "REPO=%PACKAGE_ROOT%"
if not exist "%REPO%\production\andromeda-v2\invoke-production.ps1" (
  set "REPO=C:\Users\theon\GitHub\TrackPrompt-Studio"
)
if not exist "%SCRIPT%" (
  echo The helper script was not found: %SCRIPT%
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -RepositoryRoot "%REPO%" -Mode Preflight
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo Preflight stopped safely with exit code %EXITCODE%.
) else (
  echo Preflight completed. No production render or encode was started.
)
pause
exit /b %EXITCODE%
