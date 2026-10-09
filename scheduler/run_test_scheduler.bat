@echo off
REM =====================================================================
REM run_test_scheduler.bat — test 1 layer 1 dataset via Task Scheduler
REM
REM Konfigurasi saat ini: layer SILVER, dataset silver_ars_transactions
REM Ganti LAYER dan DATASET di bawah untuk test lain.
REM
REM TIDAK ADA `pause` — cocok untuk Task Scheduler.
REM =====================================================================
setlocal

cd /d "%~dp0.."

REM Aktifkan venv kalau ada
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

REM =====================================================================
REM EDIT DI BAWAH INI UNTUK GANTI LAYER/DATASET
REM =====================================================================
set LAYER=silver
set DATASET=silver_ars_transactions
REM =====================================================================

python "scheduler\run_validation.py" ^
    --layer %LAYER% ^
    --datasets %DATASET% ^
    --source s3 ^
    --mode backfill ^
    --no-build

set EXITCODE=%ERRORLEVEL%
exit /b %EXITCODE%