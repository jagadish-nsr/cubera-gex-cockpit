#!/usr/bin/env bash
# Launcher for the scheduled task — fetches NSE's F&O bhavcopy (EOD).
# Default script backfill fills gaps when an earlier evening missed a day.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
sleep 5
{
  python3 scripts/log_futures_buildup.py --symbol NIFTY
  python3 scripts/log_futures_buildup.py --symbol BANKNIFTY
} >> data/journal/futures_buildup_run.log 2>&1
