@echo off
REM =====================================================================
REM register_all_tasks.bat — daftar 3 task sekaligus (bronze/silver/gold)
REM Jalankan SEKALI sebagai Administrator.
REM =====================================================================

set PROJ=%~dp0..

echo ============================================
echo Daftarkan 3 task validasi (bronze, silver, gold)
echo ============================================
echo.

REM ---------- BRONZE (02:00) ----------
echo [1/3] Bronze — 02:00
schtasks /Create ^
  /TN "DataValidation\Bronze_Daily" ^
  /TR "\"%PROJ%\scheduler\run_bronze.bat\"" ^
  /SC DAILY ^
  /ST 02:00 ^
  /RL HIGHEST ^
  /F ^
  /RU "%USERNAME%" ^
  /RP *
if %ERRORLEVEL% NEQ 0 echo   GAGAL daftar Bronze & goto :error

REM ---------- SILVER (03:00) ----------
echo [2/3] Silver — 03:00
schtasks /Create ^
  /TN "DataValidation\Silver_Daily" ^
  /TR "\"%PROJ%\scheduler\run_silver.bat\"" ^
  /SC DAILY ^
  /ST 03:00 ^
  /RL HIGHEST ^
  /F ^
  /RU "%USERNAME%" ^
  /RP *
if %ERRORLEVEL% NEQ 0 echo   GAGAL daftar Silver & goto :error

REM ---------- GOLD (04:00) ----------
echo [3/3] Gold — 04:00
schtasks /Create ^
  /TN "DataValidation\Gold_Daily" ^
  /TR "\"%PROJ%\scheduler\run_gold.bat\"" ^
  /SC DAILY ^
  /ST 04:00 ^
  /RL HIGHEST ^
  /F ^
  /RU "%USERNAME%" ^
  /RP *
if %ERRORLEVEL% NEQ 0 echo   GAGAL daftar Gold & goto :error

echo.
echo ============================================
echo SEMUA TASK BERHASIL DIDAFTARKAN
echo ============================================
echo.
echo Cek dengan:
echo   schtasks /Query /TN "DataValidation\Bronze_Daily" /V /FO LIST
echo   schtasks /Query /TN "DataValidation\Silver_Daily" /V /FO LIST
echo   schtasks /Query /TN "DataValidation\Gold_Daily" /V /FO LIST
echo.
echo Test manual:
echo   schtasks /Run /TN "DataValidation\Bronze_Daily"
echo   schtasks /Run /TN "DataValidation\Silver_Daily"
echo   schtasks /Run /TN "DataValidation\Gold_Daily"
goto :end

:error
echo.
echo GAGAL daftar task. Pastikan dijalankan sebagai Administrator.

:end
pause