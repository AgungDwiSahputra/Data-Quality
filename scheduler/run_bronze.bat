@echo off
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

python "scheduler\run_validation.py" ^
    --layer bronze ^
    --no-build ^
    --source s3 ^
    --mode backfill

exit /b %ERRORLEVEL%