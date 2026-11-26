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
SUPERVISOR_PID_FILE="logs/prod_supervisor.pid"
INGEST_PID=""
SERVE_PID=""

if [[ -f "${SUPERVISOR_PID_FILE}" ]]; then
  EXISTING_SUP="$(cat "${SUPERVISOR_PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${EXISTING_SUP}" && "${EXISTING_SUP}" != "$$" ]] && kill -0 "${EXISTING_SUP}" 2>/dev/null; then
    echo "❌ ERROR: run_prod.sh supervisor already running (PID ${EXISTING_SUP}). Stop it first."
    exit 1
  fi
fi
echo "$$" > "${SUPERVISOR_PID_FILE}"

cleanup() {
  local code=$?
  trap - INT TERM EXIT
  rm -f "${SUPERVISOR_PID_FILE}"
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
  rm -f "${SUPERVISOR_PID_FILE}"
  echo "======================================================================"
  echo "  CUBERA GEX COCKPIT — PRODUCTION (COMBINED / legacy)"
  echo "  URL  : http://localhost:${PROD_PORT}"
  echo "======================================================================"
  CUBERA_DHAN_CHAIN_OWNER=1 exec python3 web_server.py --port "${PROD_PORT}"
fi

echo "======================================================================"
echo "  CUBERA GEX COCKPIT — PRODUCTION (DUAL: ingest + serve)"
echo "  Serve URL  : http://localhost:${PROD_PORT}"
echo "  Ingest     : scripts/run_ingest.py (heartbeat + singleton lock)"
echo "  Supervisor : PID $$"
echo "======================================================================"

trap cleanup INT TERM EXIT

INGEST_STARTED_AT=0
INGEST_CRASHES=0
INGEST_NEXT_RESTART_AT=0

SERVE_STARTED_AT=0
SERVE_CRASHES=0
SERVE_NEXT_RESTART_AT=0

calc_backoff() {
  local crashes=$1
  if (( crashes <= 1 )); then echo 2
  elif (( crashes == 2 )); then echo 5
  elif (( crashes == 3 )); then echo 15
  elif (( crashes == 4 )); then echo 30
  else echo 60
  fi
}

start_ingest() {
  local now
  now=$(date +%s)
  if [[ -n "${INGEST_PID}" ]]; then
    if (( now - INGEST_STARTED_AT >= 30 )); then
      INGEST_CRASHES=0
    else
      INGEST_CRASHES=$((INGEST_CRASHES + 1))
    fi
    local wait_s
    wait_s=$(calc_backoff "${INGEST_CRASHES}")
    if (( now < INGEST_NEXT_RESTART_AT )); then
      return
    fi
    echo "$(date '+%H:%M:%S') ingest exited (streak=${INGEST_CRASHES}, next_backoff=${wait_s}s) — restarting…"
    INGEST_NEXT_RESTART_AT=$((now + wait_s))
  fi
  INGEST_STARTED_AT=${now}
  python3 scripts/run_ingest.py >>logs/ingest.log 2>&1 &
  INGEST_PID=$!
  echo "${INGEST_PID}" > logs/ingest.pid
  echo "  ingest PID ${INGEST_PID}"
}

start_serve() {
  local now
  now=$(date +%s)
  if [[ -n "${SERVE_PID}" ]]; then
    if (( now - SERVE_STARTED_AT >= 30 )); then
      SERVE_CRASHES=0
    else
      SERVE_CRASHES=$((SERVE_CRASHES + 1))
    fi
    local wait_s
    wait_s=$(calc_backoff "${SERVE_CRASHES}")
    if (( now < SERVE_NEXT_RESTART_AT )); then
      return
    fi
    echo "$(date '+%H:%M:%S') serve exited (streak=${SERVE_CRASHES}, next_backoff=${wait_s}s) — restarting…"
    SERVE_NEXT_RESTART_AT=$((now + wait_s))
  fi
  SERVE_STARTED_AT=${now}
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
