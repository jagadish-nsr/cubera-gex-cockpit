#!/usr/bin/env bash
# ==============================================================================
# Launch Cubera GEX Cockpit in DEVELOPMENT Mode
# - Runs on Port 8650 (isolated from Production on 8640)
# - Enables Auto-Reload (--reload) for real-time development
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${DIR}"

echo "======================================================================"
echo "  🛠️ STARTING CUBERA GEX COCKPIT — DEVELOPMENT MODE"
echo "  URL  : http://localhost:8650"
echo "  Mode : Hot Reload Active"
echo "======================================================================"

python3 web_server.py --port 8650 --reload --dev
