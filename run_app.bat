@echo off
rem One-click launcher for the Nifty GEX dashboard (no terminal knowledge needed).
rem First run installs dependencies (fast afterwards), then opens the app in your browser.
cd /d "%~dp0"
echo Starting the Nifty GEX dashboard...
echo (First launch may take a minute to install dependencies.)
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
python -m streamlit run app\dashboard.py
pause
