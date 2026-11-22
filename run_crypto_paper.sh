#!/usr/bin/env bash
# Launch CryptoCubera in LOCAL PAPER Confirm mode (sim fills; no live keys).
# Sets paper flags only — never enables exec.live / live Confirm.
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

echo "======================================================================"
echo "  CryptoCubera — LOCAL PAPER Confirm (sim / demo)"
echo "  URL  : http://localhost:${PORT}"
echo "  Flags: exec.paper=1 exec.confirm=1  |  exec.live=0 (must stay off)"
echo "  Keys : none required (local sim). Optional DELTA_PAPER_* for testnet."
echo "  Smoke: GO card → Confirm → ledger → kill switch → export CSV/JSON"
echo "======================================================================"
exec python3 -m crypto.server --host 0.0.0.0 --port "${PORT}"
