@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Run setup.bat once before starting the app.
  exit /b 1
)

".venv\Scripts\python.exe" run.py
