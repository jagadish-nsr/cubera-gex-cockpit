#!/usr/bin/env bash
# Launch CryptoCubera foundation cockpit (Advisory / mock by default).
# For local paper Confirm (preferred), use ./run_crypto_paper.sh instead.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${DIR}"
mkdir -p data/crypto logs
PORT="${CRYPTO_PORT:-8760}"
echo "======================================================================"
echo "  CryptoCubera — foundation cockpit"
echo "  URL  : http://localhost:${PORT}"
echo "  Mode : advisory / mock providers (see config/crypto.yaml)"
echo "  Paper: ./run_crypto_paper.sh  (exec.live stays off)"
echo "======================================================================"
exec python3 -m crypto.server --host 0.0.0.0 --port "${PORT}"
