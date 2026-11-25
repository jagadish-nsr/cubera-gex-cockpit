#!/usr/bin/env bash
# Launcher for the scheduled task — NSE SPAN file + Dhan futures tape for the margin-stress map
cd "$(dirname "$0")" || exit
mkdir -p data/journal
python3 scripts/refresh_liquidation_inputs.py >> data/journal/liquidation_inputs_run.log 2>&1
