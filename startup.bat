@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)

set "PYTHON=.venv\Scripts\python.exe"

"%PYTHON%" -m pip install --upgrade pip
"%PYTHON%" -m pip install -r requirements.txt

if /I "%GENERATE_SAMPLE_DATA%"=="1" (
  "%PYTHON%" scripts\generate_synthetic_data.py
)

if "%HOST%"=="" set "HOST=0.0.0.0"
if "%PORT%"=="" set "PORT=8501"

if /I "%RELOAD%"=="1" (
  "%PYTHON%" -m uvicorn promo_guard.app:app --host %HOST% --port %PORT% --reload
) else (
  "%PYTHON%" -m uvicorn promo_guard.app:app --host %HOST% --port %PORT%
)
