@echo off
REM =====================================================================
REM run_full_build.bat — full cross-layer validation DENGAN build reference
REM
REM Menjalankan:
REM   1. build_reference.py  ← per dataset
REM   2. validate.py         ← per dataset
REM
REM untuk SEMUA dataset di SEMUA layer (bronze + silver + gold).
REM
REM Estimasi waktu: 1-3 jam.
REM
REM Untuk DOUBLE-CLICK manual. Ada pause supaya bisa lihat output.
REM =====================================================================
setlocal

cd /d "%~dp0.."
echo Working dir: %CD%
echo.

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

echo ============================================
echo FULL validation - SEMUA dataset di SEMUA layer
echo Mode: BUILD + VALIDATE (build_reference dijalankan)
echo Perkiraan waktu: 1-3 jam
echo ============================================
echo.

python "scheduler\run_all_layers.py" ^
    --source s3 ^
    --mode backfill

REM TIDAK ADA --no-build → build_reference.py dijalankan

set EXITCODE=%ERRORLEVEL%
echo.
echo ============================================
echo Selesai. Exit code: %EXITCODE%
echo Cek log di: scheduler\logs\
echo Cek summary di: reports\_summary_*.json
echo ============================================
pause
exit /b %EXITCODE%