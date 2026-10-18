#!/usr/bin/env bash
# ==============================================================================
# Launch Cubera GEX Cockpit in PRODUCTION Mode
# - Runs on Port 8640
# - High-stability, zero-reload execution
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${DIR}"
mkdir -p logs

echo "======================================================================"
echo "  🎯 STARTING CUBERA GEX COCKPIT — PRODUCTION MODE"
echo "  URL  : http://localhost:8640"
echo "  Mode : Stable Institutional Daemon"
echo "======================================================================"

python3 web_server.py --port 8640
