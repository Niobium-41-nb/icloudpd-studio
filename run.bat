@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Python was not found on PATH.
  echo   Install Python 3.10+ or run: py app.py
  echo.
  pause
  exit /b 1
)

python app.py %*
if errorlevel 1 (
  echo.
  echo   iCloudPD Studio stopped with an error.
  pause
)
