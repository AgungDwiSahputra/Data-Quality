@echo off
REM =====================================================================
REM register_task.bat — daftar task cross-layer ke Windows Task Scheduler
REM Jalankan SEKALI sebagai Administrator.
REM =====================================================================

set PROJ=%~dp0..
set TASK_NAME=DataValidation\CrossLayer_Daily

echo Mendaftarkan task: %TASK_NAME%
echo Batch file      : %PROJ%\scheduler\run_daily.bat
echo Jadwal          : setiap hari jam 02:00 (serial bronze → silver → gold)
echo.

schtasks /Create ^
  /TN "%TASK_NAME%" ^
  /TR "\"%PROJ%\scheduler\run_daily.bat\"" ^
  /SC DAILY ^
  /ST 02:00 ^
  /RL HIGHEST ^
  /F ^
  /RU "%USERNAME%" ^
  /RP *

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo GAGAL daftar task. Pastikan file ini dijalankan sebagai Administrator.
) else (
    echo.
    echo BERHASIL. Cek dengan:
    echo   schtasks /Query /TN "%TASK_NAME%" /V /FO LIST
    echo.
    echo Test manual:
    echo   schtasks /Run /TN "%TASK_NAME%"
)

pause