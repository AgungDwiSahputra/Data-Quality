@echo off
REM =====================================================================
REM run_test.bat — jalankan cross-layer validation untuk 3 dataset (1 per layer)
REM
REM Double-click file ini untuk running test cepat:
REM   - Bronze  : bronze_awm_water_gate
REM   - Silver  : silver_ars_transactions
REM   - Gold    : gold_ars
REM
REM Untuk ganti dataset, edit variabel BRONZE_DS, SILVER_DS, GOLD_DS di bawah.
REM =====================================================================
setlocal

cd /d "%~dp0.."
echo Working dir: %CD%
echo.

REM Aktifkan virtualenv kalau ada
if exist ".venv\Scripts\activate.bat" (
    echo Aktifkan venv: .venv
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    echo Aktifkan venv: venv
    call "venv\Scripts\activate.bat"
) else (
    echo Tidak ada venv, pakai python global.
)
echo.

REM =====================================================================
REM EDIT DATASET DI BAWAH INI KALAU MAU GANTI
REM =====================================================================
set BRONZE_DS=bronze_awm_water_gate
set SILVER_DS=silver_ars_transactions
set GOLD_DS=gold_ars
REM =====================================================================

echo ============================================
echo Test cross-layer 1 dataset per layer:
echo   Bronze : %BRONZE_DS%
echo   Silver : %SILVER_DS%
echo   Gold   : %GOLD_DS%
echo ============================================
echo.

python "scheduler\run_all_layers.py" ^
    --datasets-per-layer ^
        bronze:%BRONZE_DS% ^
        silver:%SILVER_DS% ^
        gold:%GOLD_DS% ^
    --source s3 ^
    --mode backfill ^
    --no-build

set EXITCODE=%ERRORLEVEL%
echo.
echo ============================================
echo Selesai. Exit code: %EXITCODE%
echo Cek log di: scheduler\logs\
echo Cek JSON di: reports\
echo ============================================
pause
exit /b %EXITCODE%