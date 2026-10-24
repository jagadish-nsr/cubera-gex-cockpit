#!/bin/bash
set -e

# Change to project root
cd "$(dirname "$0")/.." || exit 1

echo "Starting GEX Daemon (option chain & participant OI)..."
python3 scripts/gex_daemon.py &
DAEMON_PID=$!

echo "Starting the web cockpit..."
python3 web_server.py --port 8640

# Cleanup when the server exits
kill $DAEMON_PID
