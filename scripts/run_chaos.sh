#!/usr/bin/env bash
# Cubera chaos / fault-injection host on port 8660 (Pillar G).
# Never writes live sentinel/journal DBs. See docs/chaos-drill.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${COCKPIT_CHAOS_PORT:-8660}"
export COCKPIT_CHAOS=1
export COCKPIT_ROLE=chaos
export COCKPIT_CHAOS_PORT="$PORT"
echo "Starting chaos host on :${PORT} (Ctrl-C to stop)"
exec python3 -m uvicorn app.chaos.host:app --host 127.0.0.1 --port "$PORT" --log-level info
