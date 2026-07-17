#!/bin/bash
# Launcher for the EXPIRY-DAY 1-min gamma-blast capture. Appends ONE per-strike
# snapshot (OI/dOI + nearest-expiry premiums + gamma exposure) to
# data/journal/gamma_blast/YYYY-MM-DD.csv.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
python3 scripts/log_gamma_blast.py --once >> "data/journal/gamma_blast_run.log" 2>&1
