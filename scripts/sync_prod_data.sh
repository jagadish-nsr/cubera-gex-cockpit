#!/usr/bin/env bash
#
# Convenient wrapper for scripts/sync_prod_to_dev.py
# Usage:
#   ./scripts/sync_prod_data.sh 2026-11-03
#   ./scripts/sync_prod_data.sh 2026-11-01 2026-11-03
#   ./scripts/sync_prod_data.sh --dry-run 2026-11-03
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

python3 "${SCRIPT_DIR}/sync_prod_to_dev.py" --dev-dir "${DEV_DIR}" "$@"

