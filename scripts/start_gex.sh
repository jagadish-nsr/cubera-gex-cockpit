#!/usr/bin/env bash
# Start the Indian GEX application with the Real-time Daemon

# Check for python-dotenv
if ! python3 -c "import dotenv" &> /dev/null; then
    echo "Warning: python-dotenv not installed. The in-app token updater might not work."
fi

# Start the background daemon
echo "Starting Breeze Background Daemon..."
python3 scripts/breeze_daemon.py &
DAEMON_PID=$!

echo "Starting Streamlit Dashboard..."
python3 -m streamlit run app/dashboard.py "$@"

# When streamlit exits, kill the daemon
kill $DAEMON_PID
echo "GEX stopped."
