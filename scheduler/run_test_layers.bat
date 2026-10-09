@echo off
REM =====================================================================
REM run_test_layers.bat — jalankan cross-layer dengan dataset override
REM
REM Cara pakai:
REM   scheduler\run_test_layers.bat
REM       → semua dataset (default, setara run_daily.bat)
REM
REM   scheduler\run_test_layers.bat bronze:bronze_awm_water_gate silver:silver_ars_transactions gold:gold_ars
REM       → 1 dataset per layer
REM
REM   scheduler\run_test_layers.bat bronze:bronze_awm_water_gate bronze:bronze_awm_bridge
REM       → hanya bronze, 2 dataset
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

REM Kalau tidak ada argumen → jalankan semua dataset (default)
if "%~1"=="" (
    echo Mode: SEMUA dataset ^(default^)
    echo.
    python "scheduler\run_all_layers.py" ^
        --source s3 ^
        --mode backfill ^
        --no-build
) else (
    echo Mode: subset dataset override
    echo Argumen: %*
    echo.
    python "scheduler\run_all_layers.py" ^
        --datasets-per-layer %* ^
        --source s3 ^
        --mode backfill ^
        --no-build
)

set EXITCODE=%ERRORLEVEL%
echo.
echo ============================================
echo Selesai. Exit code: %EXITCODE%
echo Cek log di: scheduler\logs\
echo ============================================
exit /b %EXITCODE%