#!/bin/bash
# Launcher called by the scheduled task — appends ONE intraday Nifty GEX snapshot
# to data/journal/timeseries/YYYY-MM-DD.csv, plus the per-strike OI/dOI rows to
# data/journal/oi_strikes/YYYY-MM-DD.csv (same fetch).
cd "$(dirname "$0")" || exit
mkdir -p data/journal
# brief settle so Wi-Fi reconnects if we just woke from sleep (~5s)
sleep 5
python3 scripts/log_timeseries.py --once >> "data/journal/timeseries_run.log" 2>&1
