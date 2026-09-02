@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0VERIFY-GCP-VIDEO-FASTLANE.ps1" %*
exit /b %ERRORLEVEL%
