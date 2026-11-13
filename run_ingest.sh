#!/usr/bin/env bash
# ==============================================================================
# Cubera GEX Cockpit — Ingest-only process (no public HTTP)
# Writes data/journal/ingest.heartbeat for serve readiness.
# Pair with: COCKPIT_INGEST_EXTERNAL=1 ./run_prod.sh   (or dual mode in run_prod)
# ==============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${DIR}"
mkdir -p logs data/journal

echo "======================================================================"
echo "  CUBERA INGEST — broker lanes only"
echo "  Heartbeat : data/journal/ingest.heartbeat"
echo "  Role      : ingest"
echo "======================================================================"

exec python3 scripts/run_ingest.py
