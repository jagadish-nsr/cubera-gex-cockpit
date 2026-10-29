#!/usr/bin/env bash
# Launcher for the EXPIRY-DAY 1-min gamma-blast capture.
# One shot covers Nifty, Bank Nifty, and Sensex. Each index is skipped unless
# its own nearest listed expiry is today.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
python3 scripts/log_gamma_blast.py --once >> data/journal/gamma_blast_run.log 2>&1
