@echo off
REM =====================================================================
REM run_daily_h1.bat — Validasi layer BRONZE, 1 DATASET (test)
REM
REM Dataset: bronze_estate_mapping (master snapshot)
REM
REM Karakteristik:
REM   - --layers bronze                        : hanya layer bronze
REM   - --datasets-per-layer bronze:<ds>       : hanya 1 dataset
REM   - TANPA --no-build                       : build_reference dijalankan
REM   - TANPA --date                           : auto H-1
REM   - --mode harian                          : SLA 2 hari
REM   - email aktif                            : kirim email + ZIP
REM   - TIDAK ADA pause                        : siap Task Scheduler
REM
REM Ganti dataset: edit BRONZE_DS di bawah.
REM =====================================================================
setlocal

cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

REM ==== EDIT DATASET DI BAWAH INI KALAU MAU GANTI ====
set BRONZE_DS=bronze_estate_mapping
REM ====================================================

python "scheduler\run_all_layers.py" ^
    --layers bronze ^
    --source s3 ^
    --mode harian

set EXITCODE=%ERRORLEVEL%
exit /b %EXITCODE%