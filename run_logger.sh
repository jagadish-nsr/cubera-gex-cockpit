#!/usr/bin/env bash
# Launcher for the scheduled task — logs today's Nifty GEX snapshot.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
sleep 15
python3 scripts/log_session.py >> data/journal/logger_run.log 2>&1
