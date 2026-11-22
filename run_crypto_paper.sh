#!/usr/bin/env bash
# Launch CryptoCubera paper ops — Manual Decision by default (copy/export levels).
# Preference: no auto trade placement. Confirm never POSTs to Delta.
#
# Default: Manual Decision (exec.live + exec.confirm); exec.live_place=0; exec.auto=0.
# Optional paper sim: CRYPTO_PAPER_SIM=1 → Paper badge (local sim fills; live off).
# Enables public Deribit/Delta market-data feeds (CRYPTO_LIVE_FEEDS=1 + feed
# flags). Collectors soft-fall back to mock fixtures if WS/REST fails — cockpit
# stays up. ETH/SOL + Gemma as already (soft-fallback when Ollama absent).
# BTC remains the default active underlying; topbar switcher can flip to ETH/SOL.
# Records ticks / GEX / signals into SQLite and opens a paper session for summary export.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${DIR}"
mkdir -p data/crypto logs
PORT="${CRYPTO_PORT:-8760}"

# --- Execution mode ----------------------------------------------------------
# Manual Decision (default): show/copy/export levels; Log decision; no exchange POST.
# Paper sim (optional): CRYPTO_PAPER_SIM=1 → local sim Confirm practice.
export CRYPTO_FLAG_EXEC_CONFIRM=1
export CRYPTO_FLAG_EXEC_LIVE_PLACE=0
export CRYPTO_FLAG_EXEC_AUTO=0

PAPER_SIM="${CRYPTO_PAPER_SIM:-0}"
if [ "${PAPER_SIM}" = "1" ] || [ "${PAPER_SIM}" = "true" ] || [ "${PAPER_SIM}" = "on" ]; then
  # Optional local sim practice — Paper badge; never live place.
  export CRYPTO_FLAG_EXEC_PAPER=1
  export CRYPTO_FLAG_EXEC_LIVE=0
  MODE_BADGE="Paper (sim practice)"
  MODE_NOTE="paper sim Confirm · CRYPTO_PAPER_SIM=1 · exec.live=0"
else
  # Default paper-ops path: Manual Decision (copy/export / Log decision).
  export CRYPTO_FLAG_EXEC_LIVE=1
  # Paper ledger optional alongside Manual Decision (off unless explicitly set).
  export CRYPTO_FLAG_EXEC_PAPER="${CRYPTO_FLAG_EXEC_PAPER:-0}"
  MODE_BADGE="Manual Decision"
  MODE_NOTE="copy/export levels · Log decision · no exchange POST"
fi

# Multi-asset for paper ops (BTC always on; ETH/SOL enabled for switcher).
# Override with CRYPTO_FLAG_ASSETS_ETH=0 / CRYPTO_FLAG_ASSETS_SOL=0 if needed.
export CRYPTO_FLAG_ASSETS_ETH="${CRYPTO_FLAG_ASSETS_ETH:-1}"
export CRYPTO_FLAG_ASSETS_SOL="${CRYPTO_FLAG_ASSETS_SOL:-1}"

# Public Deribit / Delta market-data feeds (not trading).
# Soft-fallback: providers serve mock fixtures when WS/REST miss or fail.
# Override with CRYPTO_LIVE_FEEDS=0 or CRYPTO_FLAG_FEEDS_DERIBIT=0 / DELTA=0.
export CRYPTO_LIVE_FEEDS="${CRYPTO_LIVE_FEEDS:-1}"
export CRYPTO_FLAG_FEEDS_DERIBIT="${CRYPTO_FLAG_FEEDS_DERIBIT:-1}"
export CRYPTO_FLAG_FEEDS_DELTA="${CRYPTO_FLAG_FEEDS_DELTA:-1}"
# Keep mock registered as last-resort provider (priority 50).
export CRYPTO_FLAG_FEEDS_MOCK="${CRYPTO_FLAG_FEEDS_MOCK:-1}"

FEEDS_NOTE="live env=${CRYPTO_LIVE_FEEDS} deribit=${CRYPTO_FLAG_FEEDS_DERIBIT} delta=${CRYPTO_FLAG_FEEDS_DELTA}"
if [ "${CRYPTO_LIVE_FEEDS}" = "0" ] || [ "${CRYPTO_LIVE_FEEDS}" = "false" ] || [ "${CRYPTO_LIVE_FEEDS}" = "off" ]; then
  FEEDS_NOTE="off (mock fixtures only)"
elif [ "${CRYPTO_FLAG_FEEDS_DERIBIT}" = "0" ] && [ "${CRYPTO_FLAG_FEEDS_DELTA}" = "0" ]; then
  FEEDS_NOTE="env on but venue flags off → mock"
else
  FEEDS_NOTE="${FEEDS_NOTE} · soft-fallback to mock if WS fails"
fi

# Gemma language layer (classify / GO prose / digests). On by default for paper.
# Soft-fallback: mock_when_unavailable → keywords / templates when Ollama is down.
# Override with CRYPTO_FLAG_INTEL_GEMMA=0 to force keywords-only.
export CRYPTO_FLAG_INTEL_GEMMA="${CRYPTO_FLAG_INTEL_GEMMA:-1}"
GEMMA_BASE="${CRYPTO_GEMMA_BASE_URL:-${OLLAMA_HOST:-${OLLAMA_BASE_URL:-http://localhost:11434}}}"
GEMMA_BASE="${GEMMA_BASE%/}"
GEMMA_NOTE="flag=${CRYPTO_FLAG_INTEL_GEMMA}"
if [ "${CRYPTO_FLAG_INTEL_GEMMA}" = "0" ] || [ "${CRYPTO_FLAG_INTEL_GEMMA}" = "false" ] || [ "${CRYPTO_FLAG_INTEL_GEMMA}" = "off" ]; then
  GEMMA_NOTE="off (keywords only)"
elif command -v curl >/dev/null 2>&1 && curl -sf --max-time 1 "${GEMMA_BASE}/api/tags" >/dev/null 2>&1; then
  GEMMA_NOTE="on · Ollama reachable (${GEMMA_BASE})"
else
  GEMMA_NOTE="on · Ollama absent → mock/keywords"
fi

# Recording on by default (override with CRYPTO_RECORDING=0).
export CRYPTO_RECORDING="${CRYPTO_RECORDING:-1}"
# Optional sticky session id for the run (auto-generated when unset).
# export CRYPTO_SESSION_ID=ps_my_desk_run
# Optional absolute/relative DB URL (relative paths resolve to repo root).
# export CRYPTO_DB_URL=sqlite:///./data/crypto/cubera.db

echo "======================================================================"
echo "  CryptoCubera — paper ops · ${MODE_BADGE}"
echo "  URL  : http://localhost:${PORT}"
echo "  Mode : ${MODE_NOTE}"
echo "  Flags: confirm=1 live_place=0 auto=0  |  live=${CRYPTO_FLAG_EXEC_LIVE} paper=${CRYPTO_FLAG_EXEC_PAPER}"
echo "  Feeds: ${FEEDS_NOTE}"
echo "  Assets: BTC (default) + ETH + SOL (switcher)"
echo "  Gemma : intel.gemma ${GEMMA_NOTE}"
echo "  Keys : none required (public MD). No DELTA_LIVE_* for Manual Decision."
echo "  Record: SQLite data/crypto/cubera.db + session summary"
echo "  Smoke: start → badge → Copy/Export (or Confirm paper) → session export"
echo "  Paper sim (optional): CRYPTO_PAPER_SIM=1 ./run_crypto_paper.sh"
echo "  After : python3 -m crypto.session_cli summary -o paper_session.json"
echo "======================================================================"
exec python3 -m crypto.server --host 0.0.0.0 --port "${PORT}"
