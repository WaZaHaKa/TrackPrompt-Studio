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
echo.
echo This opens the fail-closed production helper for the newest coherent release.
echo It will still require the repository's exact operator authorization and final typed confirmation.
echo It cannot bypass a release hold, stale identity, failed preflight, or missing 24-hour forecast.
echo.
choice /C YN /N /M "Continue to production helper? [Y/N]: "
if errorlevel 2 exit /b 0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -RepositoryRoot "%REPO%" -Mode StartAndEncode
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo Production helper stopped safely with exit code %EXITCODE%.
) else (
  echo Render, encoding, and structural QA monitoring completed.
)
pause
exit /b %EXITCODE%
