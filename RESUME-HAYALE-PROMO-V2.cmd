@echo off
setlocal
echo Complete gcloud auth login in your own terminal first if sign-in expired.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-hayale-promo-v2.ps1" -Create -ResumePreSubmit
if errorlevel 1 (
  echo Recovery stopped. Read the cause above. Saved receipts and reservations remain intact.
  pause
  exit /b 1
)
echo V2 reference promo and Resolve package are ready for your visual review.
pause
