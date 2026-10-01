# SparkPrompt OFF -- stop the stack, preserving the database volume.
# Double-click OFF.bat, or run this helper with:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\_stack-off.ps1

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -Path $root

function To-WslPath([string]$path) {
  $drive = $path.Substring(0, 1).ToLower()
  return '/mnt/' + $drive + '/' + ($path.Substring(3) -replace '\\', '/')
}
$wslRoot = To-WslPath $root

Write-Host ""
Write-Host "=== SparkPrompt OFF ===" -ForegroundColor Cyan

# [1] Frontend: stop ONLY the Next.js dev server holding port 3000.
Write-Host "[1/3] Frontend..." -ForegroundColor Cyan
$conn = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
  $stopped = $false
  foreach ($procId in ($conn | Select-Object -ExpandProperty OwningProcess -Unique)) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match 'next') {
      Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
      Write-Host "  frontend: stopped (pid $procId)"
      $stopped = $true
    } else {
      Write-Host "  frontend: port 3000 owned by a non-Next process (pid $procId) - left alone." -ForegroundColor Yellow
    }
  }
  if (-not $stopped) { Write-Host "  frontend: no Next.js process holding port 3000 was found." -ForegroundColor Yellow }
} else {
  Write-Host "  frontend: not running (skip)" -ForegroundColor DarkGray
}

# [2] Backend: stop FastAPI in WSL (scoped to this project).
Write-Host "[2/3] API..." -ForegroundColor Cyan
wsl -d Ubuntu -- bash "$wslRoot/scripts/backend-off.sh"

# [3] Database: stop the container but KEEP the named volume.
Write-Host "[3/3] Database..." -ForegroundColor Cyan
docker compose -f "infra/docker-compose.yml" down 2>&1 | ForEach-Object { "$_" } | Out-Host

Write-Host ""
Write-Host "SparkPrompt stopped." -ForegroundColor Green
Write-Host "  Database volume 'sparkprompt_pgdata' is PRESERVED." -ForegroundColor Green
Write-Host "  Your prompts, projects and settings stay for the next ON."