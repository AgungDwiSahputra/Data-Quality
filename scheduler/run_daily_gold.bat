@echo off
REM =====================================================================
REM run_daily_gold_h1.bat
REM Validasi seluruh dataset layer GOLD
REM
REM - hanya layer gold
REM - semua dataset gold
REM - build_reference dijalankan
REM - tanggal otomatis H-1
REM - mode harian
REM - email aktif
REM - tanpa pause, siap Task Scheduler
REM =====================================================================

setlocal

cd /d "%~dp0.."

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

python "scheduler\run_all_layers.py" ^
    --layers gold ^
    --source s3 ^
    --mode harian

set EXITCODE=%ERRORLEVEL%
exit /b %EXITCODE%