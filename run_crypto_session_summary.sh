#!/usr/bin/env bash
# Export the latest CryptoCubera paper recording session summary (local paper only).
# Does not start the server and never enables exec.live.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${DIR}"

FORMAT="${1:-json}"
OUT="${2:-}"

if [[ "${FORMAT}" != "json" && "${FORMAT}" != "csv" ]]; then
  echo "Usage: $0 [json|csv] [output_path]" >&2
  exit 2
fi

if [[ -n "${OUT}" ]]; then
  exec python3 -m crypto.session_cli summary --format "${FORMAT}" -o "${OUT}"
fi
exec python3 -m crypto.session_cli summary --format "${FORMAT}"
