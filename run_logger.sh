#!/bin/bash
# Launcher called by the scheduled task — logs today's Nifty GEX open snapshot.
# Output (and any errors) are appended to data/journal/logger_run.log.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
# brief settle so Wi-Fi reconnects if we just woke from sleep (~15s)
sleep 15
python3 scripts/log_session.py >> "data/journal/logger_run.log" 2>&1
