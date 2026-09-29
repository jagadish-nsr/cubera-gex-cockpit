#!/usr/bin/env bash
# Launcher for the scheduled task — appends ONE intraday Nifty GEX snapshot
cd "$(dirname "$0")" || exit
mkdir -p data/journal
# brief settle so Wi-Fi reconnects if we just woke from sleep (~5s)
sleep 5
python3 scripts/log_timeseries.py --once >> data/journal/timeseries_run.log 2>&1
