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

# 2. Terminate supervisor first (so it cannot respawn children), then ingest + listen socket
if [[ -f "${PROD_DIR}/logs/prod_supervisor.pid" ]]; then
    OLD_SUP=$(cat "${PROD_DIR}/logs/prod_supervisor.pid" 2>/dev/null || true)
    if [[ -n "${OLD_SUP}" ]] && kill -0 "${OLD_SUP}" 2>/dev/null; then
        echo "Terminating production supervisor (PID: ${OLD_SUP})..."
        kill -15 "${OLD_SUP}" 2>/dev/null || true
    fi
    rm -f "${PROD_DIR}/logs/prod_supervisor.pid"
fi
if [[ -f "${PROD_DIR}/logs/ingest.pid" ]]; then
    OLD_INGEST=$(cat "${PROD_DIR}/logs/ingest.pid" 2>/dev/null || true)
    if [[ -n "${OLD_INGEST}" ]] && kill -0 "${OLD_INGEST}" 2>/dev/null; then
        echo "Terminating production ingest (PID: ${OLD_INGEST})..."
        kill -15 "${OLD_INGEST}" 2>/dev/null || true
    fi
fi
OLD_PID=$(lsof -nP -iTCP:${PROD_PORT} -sTCP:LISTEN -t || true)
if [[ -n "${OLD_PID}" ]]; then
    echo "Terminating current production serve listener (PID: ${OLD_PID})..."
    kill -15 ${OLD_PID} 2>/dev/null || true
fi
sleep 2

# 3. Launch rolled-back version (dual ingest + EXTERNAL serve if supported by tag, else legacy combined)
echo "Restarting production stack on port ${PROD_PORT} with ${TARGET_TAG}..."
mkdir -p "${PROD_DIR}/logs"
if [[ -f "${PROD_DIR}/scripts/run_ingest.py" ]]; then
    NEW_PIDS=$(PROD_DIR="${PROD_DIR}" PROD_PORT="${PROD_PORT}" python3 - <<'PY'
import os
import subprocess
from pathlib import Path
prod = Path(os.environ["PROD_DIR"])
(prod / "logs").mkdir(parents=True, exist_ok=True)
ingest_log = open(prod / "logs" / "ingest.log", "a")
serve_log = open(prod / "logs" / "prod.log", "a")
ingest = subprocess.Popen(
    ["python3", "scripts/run_ingest.py"],
    cwd=prod,
    stdout=ingest_log,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
env = os.environ.copy()
env["COCKPIT_INGEST_EXTERNAL"] = "1"
env["COCKPIT_ROLE"] = "serve"
serve = subprocess.Popen(
    ["python3", "web_server.py", "--port", os.environ["PROD_PORT"]],
    cwd=prod,
    stdout=serve_log,
    stderr=subprocess.STDOUT,
    env=env,
    start_new_session=True,
)
(prod / "logs" / "ingest.pid").write_text(str(ingest.pid))
(prod / "logs" / "prod_server.pid").write_text(str(serve.pid))
print(f"{ingest.pid},{serve.pid}")
PY
    )
    INGEST_NEW="${NEW_PIDS%%,*}"
    NEW_PID="${NEW_PIDS##*,}"
    echo "  • Started dual stack: Ingest PID ${INGEST_NEW} · Serve PID ${NEW_PID}"
else
    cd "${PROD_DIR}"
    nohup python3 web_server.py --port ${PROD_PORT} > "${PROD_DIR}/logs/prod.log" 2>&1 &
    NEW_PID=$!
    echo "${NEW_PID}" > "${PROD_DIR}/logs/prod_server.pid"
    cd "${DEV_DIR}"
fi

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
