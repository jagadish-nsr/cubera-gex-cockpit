@echo off
rem One-click launcher for the SIMPLE Nifty GEX dashboard (the plain-language view
rem built for daily use). First run installs dependencies (fast afterwards), then
rem opens the app in your browser. Close the black window to stop it.
cd /d "%~dp0"
echo Starting the Simple Nifty GEX dashboard...
echo (First launch may take a minute to install dependencies.)
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
rem Run on 8610, not Streamlit's default 8501: a stale/hung instance can keep 8501 bound
rem (a plain "kill and reload" doesn't always free it) and serve a white screen. 8610 sidesteps that.
python -m streamlit run app\simple_dashboard.py --server.port 8610
pause
