@echo off
setlocal
cd /d "%~dp0"

if not exist "requirements.lock.txt" (
  echo requirements.lock.txt is missing. Restore the project files before setup.
  exit /b 1
)

if exist ".venv\Scripts\python.exe" goto verify_venv
if defined SUWAN_PYTHON goto use_configured_python

where py >nul 2>nul
if not errorlevel 1 goto use_py_launcher

where python >nul 2>nul
if not errorlevel 1 goto use_python_command
goto no_python

:use_configured_python
"%SUWAN_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
if errorlevel 1 goto wrong_python
"%SUWAN_PYTHON%" -m venv .venv
if errorlevel 1 goto setup_failed
goto verify_venv

:use_py_launcher
py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
if errorlevel 1 goto no_python
py -3.12 -m venv .venv
if errorlevel 1 goto setup_failed
goto verify_venv

:use_python_command
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
if errorlevel 1 goto no_python
python -m venv .venv
if errorlevel 1 goto setup_failed
goto verify_venv

:verify_venv
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul
if errorlevel 1 goto wrong_python
".venv\Scripts\python.exe" -m pip --disable-pip-version-check install -r requirements.lock.txt
if errorlevel 1 goto setup_failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto setup_failed
if not exist "config.yaml" copy "config.example.yaml" "config.yaml" >nul
if not exist ".env" copy ".env.example" ".env" >nul
echo Setup complete. Start the app with start.bat.
exit /b 0

:no_python
echo Python 3.12 is required. Install Python 3.12 or set SUWAN_PYTHON to its executable path.
exit /b 2

:wrong_python
echo The selected Python must be version 3.12. Existing environments are preserved.
exit /b 2

:setup_failed
echo Setup failed. Review the error above; no Python runtime is installed by this script.
exit /b 3
