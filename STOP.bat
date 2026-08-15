@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$pidFile = '.sparkprompt-server.pid'; if (!(Test-Path $pidFile)) { Write-Host 'No SparkPrompt server is currently tracked.'; exit 0 }; $serverId = (Get-Content $pidFile | Select-Object -First 1); if ($serverId -match '^\d+$') { $process = Get-Process -Id $serverId -ErrorAction SilentlyContinue; if ($process) { Stop-Process -Id $serverId -Force; Write-Host 'SparkPrompt server stopped.' } else { Write-Host 'The tracked server was already stopped.' } }; Remove-Item $pidFile -Force -ErrorAction SilentlyContinue"
pause
