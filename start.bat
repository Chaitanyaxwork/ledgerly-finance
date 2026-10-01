@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Could not create a virtual environment. Is Python 3 installed and on PATH?
    pause
    exit /b 1
  )
)
call .venv\Scripts\activate.bat

python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Dependency installation failed. Check the Python installation and internet connection.
  pause
  exit /b 1
)
python run.py
pause
