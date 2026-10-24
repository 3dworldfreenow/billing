@echo off
rem Double-click to start the billing app on a Windows counter PC.
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting up, please wait...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
start "" http://localhost:5000
.venv\Scripts\python app.py
pause
