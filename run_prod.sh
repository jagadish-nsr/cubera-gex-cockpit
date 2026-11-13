#!/usr/bin/env bash
# ==============================================================================
# Launch Cubera GEX Cockpit in PRODUCTION Mode
# - Serve on port 8640 (HTTP / WS telemetry / Admin)
# - Separate ingest process (spot/chain/rec) with heartbeat
# - Set COCKPIT_COMBINED=1 to fall back to single-process (legacy)
# ==============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${DIR}"
mkdir -p logs data/journal

PROD_PORT="${COCKPIT_PORT:-8640}"
INGEST_PID=""
SERVE_PID=""

cleanup() {
  local code=$?
  trap - INT TERM EXIT
  if [[ -n "${SERVE_PID}" ]] && kill -0 "${SERVE_PID}" 2>/dev/null; then
    kill -15 "${SERVE_PID}" 2>/dev/null || true
  fi
  if [[ -n "${INGEST_PID}" ]] && kill -0 "${INGEST_PID}" 2>/dev/null; then
    kill -15 "${INGEST_PID}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit "${code}"
}

if [[ "${COCKPIT_COMBINED:-0}" == "1" ]]; then
  echo "======================================================================"
  echo "  CUBERA GEX COCKPIT — PRODUCTION (COMBINED / legacy)"
  echo "  URL  : http://localhost:${PROD_PORT}"
  echo "======================================================================"
  exec python3 web_server.py --port "${PROD_PORT}"
fi

echo "======================================================================"
echo "  CUBERA GEX COCKPIT — PRODUCTION (DUAL: ingest + serve)"
echo "  Serve URL : http://localhost:${PROD_PORT}"
echo "  Ingest    : scripts/run_ingest.py (heartbeat)"
echo "======================================================================"

trap cleanup INT TERM EXIT

start_ingest() {
  if [[ -n "${INGEST_PID}" ]]; then
    echo "$(date '+%H:%M:%S') ingest exited — restarting…"
  fi
  python3 scripts/run_ingest.py >>logs/ingest.log 2>&1 &
  INGEST_PID=$!
  echo "${INGEST_PID}" > logs/ingest.pid
  echo "  ingest PID ${INGEST_PID}"
}

start_serve() {
  if [[ -n "${SERVE_PID}" ]]; then
    echo "$(date '+%H:%M:%S') serve exited — restarting…"
  fi
  COCKPIT_INGEST_EXTERNAL=1 COCKPIT_ROLE=serve \
    python3 web_server.py --port "${PROD_PORT}" >>logs/prod.log 2>&1 &
  SERVE_PID=$!
  echo "${SERVE_PID}" > logs/prod_server.pid
  echo "  serve  PID ${SERVE_PID}"
}

start_ingest
start_serve

# Poll — macOS /bin/bash 3.2 has no wait -n
while true; do
  if ! kill -0 "${INGEST_PID}" 2>/dev/null; then
    start_ingest
  fi
  if ! kill -0 "${SERVE_PID}" 2>/dev/null; then
    start_serve
  fi
  sleep 2
done
