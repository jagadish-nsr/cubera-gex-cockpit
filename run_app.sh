#!/bin/bash
# One-click launcher for the Nifty GEX dashboard.
# First run installs dependencies (fast afterwards), then opens the app in your browser.
cd "$(dirname "$0")" || exit
echo "Starting the Nifty GEX dashboard..."
echo "(First launch may take a minute to install dependencies.)"
python3 -m pip install -r requirements.txt --quiet --disable-pip-version-check
python3 -m streamlit run app/dashboard.py
