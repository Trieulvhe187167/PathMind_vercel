@echo off
chcp 65001 >nul
rem ==== PathMind dev launcher: backend :8001 + frontend :3782 ====
set ROOT=%~dp0
if "%ROOT:~-1%"=="\" set ROOT=%ROOT:~0,-1%
cd /d "%ROOT%"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
rem Demo payment mode shows the "simulate payment" button. Set to false in production.
rem Private settings (VietQR / bank / SMTP credentials): local.env.bat, never committed.
if exist "%ROOT%\local.env.bat" call "%ROOT%\local.env.bat"
if "%BILLING_DEMO_MODE%"=="" set BILLING_DEMO_MODE=true
set PYTHONPATH=%ROOT%
set PYTHONIOENCODING=utf-8
set PATHMIND_API_BASE_URL=http://127.0.0.1:8001
set BACKEND_PORT=8001
set PATHMIND_AUTH_ENABLED=true
set NEXT_PUBLIC_AUTH_ENABLED=true
start "PathMind backend" /min cmd /c "python -m uvicorn pathmind.api.main:app --host 127.0.0.1 --port 8001 > %ROOT%\logs\backend.log 2>&1"
rem Frontend: optimized production build by default (fast, smooth).
rem "start.bat dev" runs the Next.js development server (hot reload, slower).
rem The production build is redone automatically when files under web\ change;
rem that first start takes a few minutes (see logs\frontend.log).
if /i "%~1"=="dev" (
  cd /d "%ROOT%\web"
  start "PathMind frontend" /min cmd /c "npm run dev -- -p 3782 > %ROOT%\logs\frontend.log 2>&1"
  echo PathMind [dev] is starting: http://127.0.0.1:3782  ^(logs in %ROOT%\logs^)
) else (
  start "PathMind frontend" /min cmd /c "python %ROOT%\scripts\start_web_prod.py > %ROOT%\logs\frontend.log 2>&1"
  echo PathMind is starting: http://127.0.0.1:3782  ^(logs in %ROOT%\logs; first start after an update builds the web app, a few minutes^)
)
