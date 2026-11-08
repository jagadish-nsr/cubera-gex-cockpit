#!/usr/bin/env bash
# Start the futures tape for a Muhurat session. No-ops unless today is listed.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
python3 - <<'PY' || exit 0
from data.session_calendar import is_muhurat_session
import sys
sys.exit(0 if is_muhurat_session() else 1)
PY
exec ./run_big_money.sh
