@echo off
setlocal EnableExtensions
pushd "%~dp0"
title SparkPrompt ON

echo === SparkPrompt ON ===

rem ---- [0] WSL present? (API runs inside Ubuntu) ----
wsl -d Ubuntu -e echo ok >nul 2>&1
if errorlevel 1 (
  echo [ERROR] WSL distro 'Ubuntu' is not available.
  echo         Install it first with wsl --install and retry.
  goto fail
)

rem ---- [1] PostgreSQL (Docker Desktop). Volume is never removed. ----
echo [1/5] Database...
call docker compose -f "infra/docker-compose.yml" up -d --no-recreate >nul 2>&1
set "pgUp="
for /l %%i in (1,1,30) do (
  for /f "usebackq delims=" %%h in (`call docker inspect -f "{{.State.Health.Status}}" sparkprompt-postgres 2^>nul`) do if "%%h"=="healthy" set "pgUp=1"
  if defined pgUp goto pgok
  ping -n 2 127.0.0.1 >nul
)
echo   postgres: not healthy within 30s - check Docker Desktop
goto api
:pgok
echo   postgres: healthy

:api
rem ---- [2] FastAPI backend (detached inside WSL via setsid+nohup) ----
echo [2/5] API...
set "winRoot=%~dp0"
set "drive=%winRoot:~0,1%"
if /i "%drive%"=="A" set "drive=a"
if /i "%drive%"=="B" set "drive=b"
if /i "%drive%"=="C" set "drive=c"
if /i "%drive%"=="D" set "drive=d"
if /i "%drive%"=="E" set "drive=e"
if /i "%drive%"=="F" set "drive=f"
if /i "%drive%"=="G" set "drive=g"
if /i "%drive%"=="H" set "drive=h"
set "wslRoot=/mnt/%drive%/%winRoot:~3%"
set "wslRoot=%wslRoot:\=/%"
if "%wslRoot:~-1%"=="/" set "wslRoot=%wslRoot:~0,-1%"
wsl -d Ubuntu -- bash "%wslRoot%/scripts/backend-on.sh"

rem ---- [3] Wait for /api/health ----
echo [3/5] API health...
set "apiUp="
for /l %%i in (1,1,20) do (
  curl.exe -s -o NUL --max-time 2 http://127.0.0.1:8000/api/health
  if not errorlevel 1 set "apiUp=1"
  if defined apiUp goto apiok
  ping -n 2 127.0.0.1 >nul
)
goto apiafter
:apiok
set "apiUp=1"
:apiafter
if defined apiUp (echo   api: healthy at http://localhost:8000/api/health) else (echo   api: not responding yet - log: /tmp/sparkprompt-uvicorn.log)

rem ---- [4] Next.js frontend (Windows host) ----
echo [4/5] Frontend...
curl.exe -s -o NUL --max-time 2 http://127.0.0.1:3000
if not errorlevel 1 (
  echo   frontend: already running
  goto feup
)
echo   frontend: starting Next.js (log: %TEMP%\spark-next.log)
start "" /min cmd /c "cd /d "%~dp0frontend" && npm run dev > "%TEMP%\spark-next.log" 2>&1" >nul 2>&1
set "feUp="
for /l %%i in (1,1,60) do (
  curl.exe -s -o NUL --max-time 2 http://127.0.0.1:3000
  if not errorlevel 1 set "feUp=1"
  if defined feUp goto feup
  ping -n 2 127.0.0.1 >nul
)
echo   frontend: not online after 60s (log: %TEMP%\spark-next.log)
goto browser
:feup
echo   frontend: online at http://localhost:3000

:browser
rem ---- [5] Open the app ----
echo [5/5] Browser...
start "" "http://localhost:3000"
echo.
echo SparkPrompt is up.
echo   Frontend : http://localhost:3000
echo   API      : http://localhost:8000/api/health
echo   Database : sparkprompt-postgres (volume preserved)
goto end

:fail
echo [ERROR] SparkPrompt could not start. Check the messages above.

:end
popd
pause