#!/usr/bin/env bash
# Sunday Muhurat afternoon collectors. No-ops unless today is a listed Muhurat session.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
python3 - <<'PY' || exit 0
from data.session_calendar import is_muhurat_session
import sys
sys.exit(0 if is_muhurat_session() else 1)
PY
{
  echo "$(date '+%Y-%m-%d %H:%M:%S') muhurat collect"
  python3 scripts/log_timeseries.py || true
} >> data/journal/timeseries_run.log 2>&1
