@echo off
setlocal
cd /d "%~dp0"
set "PORT=8000"

if exist ".sparkprompt-server.pid" (
  echo SparkPrompt is already running or was not stopped cleanly.
  echo Open http://127.0.0.1:%PORT% in your browser.
  start "" "http://127.0.0.1:%PORT%"
  pause
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$python = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } elseif (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { $null }; if (-not $python) { Write-Host 'Python was not found. Install Python 3, then run this file again.' -ForegroundColor Red; exit 1 }; $args = if ($python -eq 'py') { @('-3','-m','http.server','8000','--bind','127.0.0.1') } else { @('-m','http.server','8000','--bind','127.0.0.1') }; $p = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru; $p.Id | Set-Content -Encoding ascii '.sparkprompt-server.pid'"
if errorlevel 1 (
  pause
  exit /b 1
)

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:%PORT%"
echo SparkPrompt is running at http://127.0.0.1:%PORT%
echo Use STOP.bat when you are finished.
pause
