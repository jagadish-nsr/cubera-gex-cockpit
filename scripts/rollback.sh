#!/usr/bin/env bash
# ==============================================================================
# Cubera GEX Cockpit — Instant Rollback Tool
# Reverts Production environment to a previous known-good release tag with one command.
#
# Usage:
#   ./scripts/rollback.sh           # Rolls back to previous release tag
#   ./scripts/rollback.sh v5.3.0    # Rolls back to explicit release tag
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROD_DIR="$(cd "${DEV_DIR}/../cubera-gex-cockpit-prod" 2>/dev/null && pwd || echo "")"
PROD_PORT=8640
HEALTH_URL="http://localhost:${PROD_PORT}/api/health"
RELEASE_LOG="${DEV_DIR}/logs/releases.log"

echo "======================================================================"
echo "  ⏪ CUBERA GEX COCKPIT — INSTANT PRODUCTION ROLLBACK"
echo "======================================================================"

if [[ -z "${PROD_DIR}" || ! -d "${PROD_DIR}" ]]; then
    echo "❌ ERROR: Production worktree not found at '../cubera-gex-cockpit-prod'."
    exit 1
fi

TARGET_TAG="${1:-}"

if [[ -z "${TARGET_TAG}" ]]; then
    # Pick the second-to-last tag from git
    TARGET_TAG=$(git -C "${DEV_DIR}" tag -l "v*" --sort=-v:refname | sed -n '2p')
    if [[ -z "${TARGET_TAG}" ]]; then
        echo "❌ ERROR: No previous release tag found to roll back to."
        exit 1
    fi
fi

echo "Rolling back Production to: ${TARGET_TAG}..."

# 1. Checkout target tag in prod worktree
git -C "${PROD_DIR}" checkout "${TARGET_TAG}"

# 2. Terminate existing prod server
OLD_PID=$(lsof -ti :${PROD_PORT} || echo "")
if [[ -n "${OLD_PID}" ]]; then
    echo "Terminating current production process (PID: ${OLD_PID})..."
    kill -15 ${OLD_PID} || true
    sleep 2
fi

# 3. Launch rolled-back version
echo "Restarting production server on port ${PROD_PORT} with ${TARGET_TAG}..."
cd "${PROD_DIR}"
nohup python3 web_server.py --port ${PROD_PORT} > "${PROD_DIR}/logs/prod.log" 2>&1 &
NEW_PID=$!
echo "${NEW_PID}" > "${PROD_DIR}/logs/prod_server.pid"
cd "${DEV_DIR}"

# 4. Verify health + readiness schema (same keys promote Stage 5 / H2 asserts)
echo "Verifying health..."
HEALTH_PASSED=false
for i in {1..15}; do
    if curl -s -f "${HEALTH_URL}" > /tmp/rollback_health.json 2>/dev/null; then
        HEALTH_PASSED=true
        break
    fi
    sleep 1
done

if [[ "${HEALTH_PASSED}" == "true" ]]; then
    TAG_BARE="${TARGET_TAG#v}"
    python3 - <<PY
import json, sys
tag = "${TAG_BARE}"
data = json.load(open("/tmp/rollback_health.json"))
ver = str(data.get("version") or "")
if ver.replace("v", "") != tag and not ver.startswith(tag):
    print(f"ERROR: health version {ver!r} != rollback target {tag!r}", file=sys.stderr)
    sys.exit(1)
rd = data.get("readiness")
if not isinstance(rd, dict):
    print("ERROR: /api/health missing readiness object", file=sys.stderr)
    sys.exit(1)
for key in ("ready", "degrade_mode", "probes"):
    if key not in rd:
        print(f"ERROR: readiness missing {key!r}", file=sys.stderr)
        sys.exit(1)
if "ingest" not in (rd.get("probes") or {}):
    print("ERROR: readiness.probes.ingest missing", file=sys.stderr)
    sys.exit(1)
print(f"  • Rollback smoke OK: version={ver} ready={rd.get('ready')} degrade_mode={rd.get('degrade_mode')!r}")
PY
    echo "✅ SUCCESS: Rolled back cleanly to ${TARGET_TAG} on http://localhost:${PROD_PORT}"
    echo "$(date '+%Y-%m-%d %H:%M:%S') | ROLLBACK | ${TARGET_TAG} | PID: ${NEW_PID}" >> "${RELEASE_LOG}"
    exit 0
else
    echo "⚠️ WARNING: Health check probe did not respond. Check logs at ${PROD_DIR}/logs/prod.log"
    exit 1
fi
