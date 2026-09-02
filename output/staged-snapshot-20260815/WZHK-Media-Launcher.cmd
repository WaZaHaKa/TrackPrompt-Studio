@echo off
setlocal
chcp 65001 >nul
title WZHK Media Mission Control
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start-wzhk-mission-control.ps1" -RepositoryRoot "%~dp0." %*
set "WZHK_EXIT=%ERRORLEVEL%"

if not "%WZHK_EXIT%"=="0" (
    echo.
    echo WZHK Media Mission Control could not start. Exit code %WZHK_EXIT%.
)

exit /b %WZHK_EXIT%
