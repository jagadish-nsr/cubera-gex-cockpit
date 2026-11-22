#!/usr/bin/env bash
# Launch CryptoCubera in LOCAL PAPER Confirm mode (sim fills; no live keys).
# Sets paper + ETH/SOL asset flags — never enables exec.live / live Confirm.
# BTC remains the default active underlying; topbar switcher can flip to ETH/SOL.
# Records ticks / GEX / signals into SQLite and opens a paper session for summary export.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${DIR}"
mkdir -p data/crypto logs
PORT="${CRYPTO_PORT:-8760}"

# Local paper path only. Do not export CRYPTO_FLAG_EXEC_LIVE or DELTA_LIVE_*.
export CRYPTO_FLAG_EXEC_PAPER=1
export CRYPTO_FLAG_EXEC_CONFIRM=1
# Explicit refuse — preference: local paper trading only.
export CRYPTO_FLAG_EXEC_LIVE=0
export CRYPTO_FLAG_EXEC_AUTO=0

# Multi-asset for paper ops (BTC always on; ETH/SOL enabled for switcher).
# Override with CRYPTO_FLAG_ASSETS_ETH=0 / CRYPTO_FLAG_ASSETS_SOL=0 if needed.
export CRYPTO_FLAG_ASSETS_ETH="${CRYPTO_FLAG_ASSETS_ETH:-1}"
export CRYPTO_FLAG_ASSETS_SOL="${CRYPTO_FLAG_ASSETS_SOL:-1}"

# Recording on by default (override with CRYPTO_RECORDING=0).
export CRYPTO_RECORDING="${CRYPTO_RECORDING:-1}"
# Optional sticky session id for the run (auto-generated when unset).
# export CRYPTO_SESSION_ID=ps_my_desk_run
# Optional absolute/relative DB URL (relative paths resolve to repo root).
# export CRYPTO_DB_URL=sqlite:///./data/crypto/cubera.db

echo "======================================================================"
echo "  CryptoCubera — LOCAL PAPER Confirm (sim / demo)"
echo "  URL  : http://localhost:${PORT}"
echo "  Flags: exec.paper=1 exec.confirm=1  |  exec.live=0 (must stay off)"
echo "  Assets: BTC (default) + ETH + SOL (switcher)"
echo "  Keys : none required (local sim). Optional DELTA_PAPER_* for testnet."
echo "  Record: SQLite data/crypto/cubera.db + paper session summary"
echo "  Smoke: start → switch asset → Confirm → session export"
echo "  After : python3 -m crypto.session_cli summary -o paper_session.json"
echo "======================================================================"
exec python3 -m crypto.server --host 0.0.0.0 --port "${PORT}"
