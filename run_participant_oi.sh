#!/usr/bin/env bash
# Launcher for the scheduled task — fetches NSE participant-wise OI EOD
cd "$(dirname "$0")" || exit
mkdir -p data/journal
sleep 5
python3 scripts/log_participant_oi.py >> data/journal/participant_oi_run.log 2>&1
