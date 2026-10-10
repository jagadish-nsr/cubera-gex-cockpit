#!/bin/bash
# Mac/Linux one-click launcher for the Unified Nifty Cockpit.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
echo "Starting Unified Nifty Cockpit on port 8630..."
python3 -m pip install -r requirements.txt --quiet --disable-pip-version-check
python3 -m streamlit run app/cockpit.py --server.port 8630
