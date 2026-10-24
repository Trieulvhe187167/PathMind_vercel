@echo off
rem Backend tests + web type check and lint.
chcp 65001 >nul
cd /d "%~dp0"
pushd backend
..\.venv\Scripts\python -m pytest
set PYERR=%errorlevel%
popd
pushd web
call npm run typecheck
set TSERR=%errorlevel%
call npm run lint
set LINTERR=%errorlevel%
call npm run i18n:check
set I18NERR=%errorlevel%
popd
echo pytest=%PYERR% typecheck=%TSERR% lint=%LINTERR% i18n=%I18NERR%
if not "%PYERR%%TSERR%%LINTERR%%I18NERR%"=="0000" exit /b 1
