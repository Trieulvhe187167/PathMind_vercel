@echo off
rem Copy to local.env.bat (git-ignored) and fill in. start.bat loads it.
rem ---- Database: leave empty for SQLite in data\user\pathmind.db ----
rem PostgreSQL: DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/DBNAME
rem and DATABASE_SCHEMA keeps PathMind in its own schema (created on start).
rem Move existing SQLite data once with: python scripts\migrate_sqlite_to_postgres.py
set DATABASE_URL=
set DATABASE_SCHEMA=
rem ---- VNPAY (sandbox first: https://sandbox.vnpayment.vn/devreg/) ----
set VNPAY_TMN_CODE=
set VNPAY_HASH_SECRET=
set VNPAY_ENV=sandbox
set PATHMIND_PUBLIC_URL=http://localhost:3782
rem ---- Demo payment buttons: must be false on the real site ----
set BILLING_DEMO_MODE=true
