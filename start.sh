#!/usr/bin/env bash
# One-command launcher for macOS / Linux / WSL / Git Bash:  ./start.sh
set -e
cd "$(dirname "$0")"

# Pick a Python 3 interpreter
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else echo "Python 3 is not installed. Install it from https://www.python.org/downloads/"; exit 1; fi

# Create the virtual environment once
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  "$PY" -m venv .venv
fi

# Activate (Linux/macOS use bin/, Git Bash on Windows uses Scripts/)
if [ -f ".venv/bin/activate" ]; then . .venv/bin/activate
else . .venv/Scripts/activate; fi

echo "Installing dependencies..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo "Starting Ledgerly..."
python run.py
