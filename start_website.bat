@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3 and try again.
  pause
  exit /b 1
)

py -m pip install -r requirements.txt
if errorlevel 1 (
  echo Could not install the required Python packages.
  pause
  exit /b 1
)

py trail_3_web.py
pause
