@echo off
REM =====================================================================
REM run_daily_silver_h1.bat
REM Validasi seluruh dataset layer SILVER
REM
REM - hanya layer silver
REM - semua dataset silver
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
    --layers silver ^
    --source s3 ^
    --mode harian

set EXITCODE=%ERRORLEVEL%
exit /b %EXITCODE%