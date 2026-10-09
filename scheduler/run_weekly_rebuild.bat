@echo off
REM =====================================================================
REM run_weekly_rebuild.bat — rebuild baseline + validasi full
REM
REM Jalankan mingguan (mis. Minggu 01:00) untuk refresh baseline.
REM
REM Karakteristik:
REM   - TANPA --no-build → build_reference.py dijalankan untuk SEMUA dataset
REM   - TANPA --date → validasi semua partisi historis (mode backfill)
REM   - Mode backfill (SLA 260 hari) → cocok untuk historical
REM   - TIDAK ADA pause → siap untuk Task Scheduler
REM =====================================================================
setlocal

cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

REM --date TIDAK diisi DAN kita disable auto H-1 dengan 'auto'
REM TANPA --no-build → build_reference.py jalan untuk semua dataset
python "scheduler\run_all_layers.py" ^
    --date auto ^
    --source s3 ^
    --mode backfill

set EXITCODE=%ERRORLEVEL%
exit /b %EXITCODE%