#!/bin/bash
set -e

# Change to project root
cd "$(dirname "$0")/.." || exit 1

echo "Starting GEX Daemon (option chain & participant OI)..."
python3 scripts/breeze_daemon.py &
DAEMON_PID=$!

echo "Starting Indian GEX Dashboard (3-Tab)..."
python3 -m streamlit run app/dashboard.py

# Cleanup when streamlit exits
kill $DAEMON_PID
