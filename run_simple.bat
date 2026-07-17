@echo off
rem One-click launcher for the SIMPLE Nifty GEX dashboard (the plain-language view
rem built for daily use). First run installs dependencies (fast afterwards), then
rem opens the app in your browser. Close the black window to stop it.
cd /d "%~dp0"
echo Starting the Simple Nifty GEX dashboard...
echo (First launch may take a minute to install dependencies.)
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
python -m streamlit run app\simple_dashboard.py
pause
