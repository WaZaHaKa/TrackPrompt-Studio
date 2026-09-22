@echo off
setlocal
set "HAYALE_PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not exist "%HAYALE_PWSH%" set "HAYALE_PWSH=%LOCALAPPDATA%\Programs\PowerShell\7\pwsh.exe"
if not exist "%HAYALE_PWSH%" set "HAYALE_PWSH=%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"
if not exist "%HAYALE_PWSH%" set "HAYALE_PWSH=pwsh.exe"
"%HAYALE_PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0FINISH-HAYALE-GCP.ps1" %*
set "HAYALE_EXIT=%ERRORLEVEL%"
if not "%HAYALE_EXIT%"=="0" pause
exit /b %HAYALE_EXIT%
