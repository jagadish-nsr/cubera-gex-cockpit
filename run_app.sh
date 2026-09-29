#!/usr/bin/env bash
# One-click launcher for the Nifty GEX dashboard.
cd "$(dirname "$0")" || exit
echo "Starting the Nifty GEX dashboard..."
python3 -m pip install -r requirements.txt --quiet --disable-pip-version-check
python3 -m streamlit run app/dashboard.py
