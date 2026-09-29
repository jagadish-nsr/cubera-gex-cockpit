#!/usr/bin/env bash
# One-click launcher for the SIMPLE Nifty GEX dashboard.
cd "$(dirname "$0")" || exit
echo "Starting the Simple Nifty GEX dashboard..."
python3 -m pip install -r requirements.txt --quiet --disable-pip-version-check
python3 -m streamlit run app/simple_dashboard.py
