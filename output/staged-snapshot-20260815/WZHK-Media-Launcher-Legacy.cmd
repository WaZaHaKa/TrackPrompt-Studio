@echo off
setlocal
chcp 65001 >nul
title WZHK Media Mission Control - Legacy
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0wzhk-media-control-center.ps1" %*
set "WZHK_EXIT=%ERRORLEVEL%"

if not "%WZHK_EXIT%"=="0" (
    echo.
    echo Legacy WZHK Media Mission Control exited with code %WZHK_EXIT%.
    pause
)

exit /b %WZHK_EXIT%
