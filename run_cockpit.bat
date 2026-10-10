@echo off
rem One-click launcher for the Unified Nifty Cockpit (GEX Map + Paper Journal).
rem Runs the cockpit dashboard in your browser. Close the black window to stop it.
cd /d "%~dp0"
echo Starting Unified Nifty Cockpit...
echo (First launch may take a minute to install dependencies.)
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
rem Port 8630 to avoid clashing with simple_dashboard (8610) or main dashboard (8501/8502)
python -m streamlit run app\cockpit.py --server.port 8630
pause
