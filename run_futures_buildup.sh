#!/usr/bin/env bash
# Launcher for the scheduled task — fetches NSE's F&O bhavcopy (EOD)
cd "$(dirname "$0")" || exit
mkdir -p data/journal
sleep 5
python3 scripts/log_futures_buildup.py >> data/journal/futures_buildup_run.log 2>&1
