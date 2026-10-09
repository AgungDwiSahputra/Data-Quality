@echo off
REM =====================================================================
REM run_daily.bat — entry point untuk Windows Task Scheduler
REM
REM Menjalankan cross-layer validation DENGAN build reference:
REM   1. build_reference.py  ← per dataset
REM   2. validate.py         ← per dataset
REM
REM untuk SEMUA dataset di SEMUA layer (bronze + silver + gold).
REM
REM TIDAK ADA pause → Task Scheduler tidak hang.
REM =====================================================================
setlocal

cd /d "%~dp0.."

REM Aktifkan virtualenv kalau ada
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

REM TIDAK ADA --no-build → build_reference.py dijalankan
python "scheduler\run_all_layers.py" ^
    --source s3 ^
    --mode backfill

set EXITCODE=%ERRORLEVEL%
exit /b %EXITCODE%