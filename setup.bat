@echo off
rem One-time setup (and after pulling changes): Python venv + packages, web packages, web build.
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
if not exist .venv (
  echo Creating Python virtual environment...
  python -m venv .venv || (echo Python 3.11+ is required & exit /b 1)
)
echo Installing Python packages...
.venv\Scripts\python -m pip install --disable-pip-version-check -q -r backend\requirements.txt || exit /b 1
if exist local.env.bat call local.env.bat
if defined DATABASE_URL (
  echo Applying database migrations...
  pushd backend
  ..\.venv\Scripts\python -m alembic upgrade head || (popd & exit /b 1)
  popd
) else (
  echo No DATABASE_URL: the app will use SQLite in data\. For PostgreSQL run backend\scripts\create_database.py first.
)
echo Installing web packages...
pushd web
call npm install --no-fund --no-audit || (popd & exit /b 1)
echo Building the web app...
call npm run build || (popd & exit /b 1)
popd
echo Setup finished. Run start.bat
