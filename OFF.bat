@echo off
:: SparkPrompt OFF -- stop frontend + API + database, keep data volume.
pushd "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_stack-off.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] SparkPrompt could not be stopped cleanly. See messages above.
) else (
  echo.
  echo SparkPrompt is stopped. Your data is safe for next time.
)
popd
pause