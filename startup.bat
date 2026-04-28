@echo off
setlocal EnableExtensions
title Promo Coherence Guard

cd /d "%~dp0"
echo.
echo  Promo Coherence Guard
echo  ----------------------
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python was not found in PATH.
  echo Install Python 3.11 or newer from https://www.python.org/downloads/
  echo Make sure "Add python.exe to PATH" is checked during setup.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo ERROR: Could not create .venv
    pause
    exit /b 1
  )
)

set "PYTHON=.venv\Scripts\python.exe"

echo Installing / updating dependencies ...
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :pipfail
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :pipfail

if /I "%GENERATE_SAMPLE_DATA%"=="1" (
  echo Regenerating sample data ...
  "%PYTHON%" scripts\generate_synthetic_data.py
)

if "%HOST%"=="" set "HOST=0.0.0.0"
if "%PORT%"=="" set "PORT=8501"

echo.
echo  Starting server at http://localhost:%PORT%/
echo  Press Ctrl+C to stop.
echo.

if /I "%RELOAD%"=="1" (
  "%PYTHON%" -m uvicorn promo_guard.app:app --host %HOST% --port %PORT% --reload
) else (
  "%PYTHON%" -m uvicorn promo_guard.app:app --host %HOST% --port %PORT%
)
goto :eof

:pipfail
echo ERROR: pip install failed.
pause
exit /b 1
