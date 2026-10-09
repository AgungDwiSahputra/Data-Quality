@echo off
REM =====================================================================
REM run_full.bat — jalankan cross-layer validation untuk SEMUA dataset
REM (59 bronze + 4 silver + 6 gold = 69 dataset)
REM
REM Double-click untuk running produksi (full).
REM Waktu estimasi: 30-60 menit.
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
echo Running FULL validation (semua dataset)
echo Perkiraan waktu: 30-60 menit
echo ============================================
echo.

python "scheduler\run_all_layers.py" ^
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