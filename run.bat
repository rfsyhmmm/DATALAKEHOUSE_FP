@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Virtual environment not found, creating...
    python -m venv .venv
)

echo [SETUP] Installing dependencies...
.venv\Scripts\pip install -r requirements.txt --quiet

echo.
echo [RUN] Starting extraction pipeline...
.venv\Scripts\python.exe dummy_data\run_extractions.py

echo.
if %ERRORLEVEL% == 0 (
    echo [DONE] Pipeline finished successfully.
) else (
    echo [ERROR] Pipeline failed with exit code %ERRORLEVEL%.
)
pause
