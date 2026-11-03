#!/usr/bin/env bash
# Expiry-afternoon option-tick capture. One process, 13:40 IST through the F&O close.
# Nifty, Bank Nifty, and Sensex are each skipped unless that index expires today.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
python3 scripts/run_blast_ticks.py --market-hours --symbols NIFTY,BANKNIFTY,SENSEX >> data/journal/blast_ticks_run.log 2>&1
