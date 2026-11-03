#!/usr/bin/env bash
# Expiry-afternoon option-tick capture. Cron fires at 12:55 IST; the script waits for
# the 13:00 freeze and runs through the F&O close. Each index is skipped unless it expires today.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
python3 scripts/run_blast_ticks.py --market-hours --freeze 13:00 --symbols NIFTY,BANKNIFTY,SENSEX >> data/journal/blast_ticks_run.log 2>&1
