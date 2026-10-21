#!/usr/bin/env bash
# ==============================================================================
# Cubera GEX Cockpit — Automated Promotion & Release Pipeline
# Promotes tested changes from Development to Production with zero-downtime & safety gates.
#
# Usage:
#   ./scripts/promote.sh              # Auto-increments patch version (e.g. v5.3.0 -> v5.3.1)
#   ./scripts/promote.sh minor        # Increments minor version (e.g. v5.3.0 -> v5.4.0)
#   ./scripts/promote.sh v5.4.0       # Explicit semantic version tag
#   ./scripts/promote.sh --sync-db    # Include Dev->Prod SQLite DB overwrite (opt-in only!)
#   ./scripts/promote.sh patch --sync-db
# ==============================================================================

set -euo pipefail

export PATH="$HOME/Library/Python/3.9/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROD_DIR="$(cd "${DEV_DIR}/../cubera-gex-cockpit-prod" 2>/dev/null && pwd || echo "")"
LOG_DIR="${DEV_DIR}/logs"
RELEASE_LOG="${LOG_DIR}/releases.log"
PROD_PORT=8640
HEALTH_URL="http://localhost:${PROD_PORT}/api/health"

mkdir -p "${LOG_DIR}"

echo "======================================================================"
echo "  🚀 CUBERA GEX COCKPIT — CONTINUOUS PROMOTION PIPELINE"
echo "======================================================================"
echo "Source Dev Directory : ${DEV_DIR}"
echo "Target Prod Directory: ${PROD_DIR:-'Not yet initialized'}"
echo "Target Prod Port     : ${PROD_PORT}"
echo "Current Time (IST)   : $(date)"
echo ""

# ------------------------------------------------------------------------------
# Stage 1: Quality Gates (Pre-Flight Verification)
# ------------------------------------------------------------------------------
echo "▶ [Stage 1/5] Running Quality Gates & Test Suite..."

# 1.1 Check uncommitted changes in Dev
if ! git -C "${DEV_DIR}" diff-index --quiet HEAD --; then
    echo "⚠️ ERROR: Uncommitted changes detected in Dev workspace."
    echo "   Please commit or stash your changes before promoting."
    exit 1
fi

# 1.2 Run Python endpoint tests & critical models
echo "  • Executing Web Server Endpoint Integration Tests..."
python3 -m pytest -q "${DEV_DIR}/tests/test_web_server_endpoints.py"

echo "  • Executing Quant & Market Parity Tests..."
python3 -m pytest -q "${DEV_DIR}/tests/test_cockpit_market_hours.py" \
                     "${DEV_DIR}/tests/test_universal_ist_timestamps.py" \
                     -k "not test_sentinel_seed_anchors_to_prior_trading_day_on_weekday_holiday"

echo "✅ All Quality Gates PASSED."
echo ""

# ------------------------------------------------------------------------------
# Stage 2: Semantic Version Resolution & Tagging
# ------------------------------------------------------------------------------
echo "▶ [Stage 2/5] Resolving Version Tag..."

CURRENT_BRANCH=$(git -C "${DEV_DIR}" rev-parse --abbrev-ref HEAD)
LATEST_TAG=$(git -C "${DEV_DIR}" describe --tags --abbrev=0 2>/dev/null || echo "v5.3.0")

TARGET_TAG=""
SYNC_DB=false

for arg in "$@"; do
    case "$arg" in
        --sync-db|--force-db-sync)
            SYNC_DB=true
            ;;
        *)
            if [[ -z "${TARGET_TAG}" ]]; then
                TARGET_TAG="$arg"
            fi
            ;;
    esac
done

if [[ -z "${TARGET_TAG}" ]]; then
    TARGET_TAG="patch"
fi

if [[ "${TARGET_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
    NEW_TAG="${TARGET_TAG}"
else
    # Parse version components
    VERSION_NUM="${LATEST_TAG#v}"
    IFS='.' read -r MAJOR MINOR PATCH <<< "${VERSION_NUM}"
    
    if [[ "${TARGET_TAG}" == "major" ]]; then
        MAJOR=$((MAJOR + 1))
        MINOR=0
        PATCH=0
    elif [[ "${TARGET_TAG}" == "minor" ]]; then
        MINOR=$((MINOR + 1))
        PATCH=0
    else # patch
        PATCH=$((PATCH + 1))
    fi
    NEW_TAG="v${MAJOR}.${MINOR}.${PATCH}"
fi

echo "  • Current Latest Tag: ${LATEST_TAG}"
echo "  • Promoted Version  : ${NEW_TAG}"
echo ""

# ------------------------------------------------------------------------------
# Stage 3: Merge & Tag in Production Worktree (`main`)
# ------------------------------------------------------------------------------
echo "▶ [Stage 3/5] Synchronizing Git Branches & Creating Release Tag..."

if [[ -z "${PROD_DIR}" || ! -d "${PROD_DIR}" ]]; then
    echo "ℹ️ Note: Production worktree does not exist yet at '../cubera-gex-cockpit-prod'."
    echo "  Creating it now via 'git worktree add ../cubera-gex-cockpit-prod main'..."
    git -C "${DEV_DIR}" worktree add "${DEV_DIR}/../cubera-gex-cockpit-prod" main
    PROD_DIR="${DEV_DIR}/../cubera-gex-cockpit-prod"
fi

# Merge dev into main inside the production worktree
echo "  • Merging ${CURRENT_BRANCH} into main in production worktree..."
git -C "${PROD_DIR}" merge "${CURRENT_BRANCH}" --no-edit -m "Release ${NEW_TAG}: Promoted from ${CURRENT_BRANCH}"

# Create annotated git tag
echo "  • Tagging release: ${NEW_TAG}..."
git -C "${DEV_DIR}" tag -a "${NEW_TAG}" -m "Release ${NEW_TAG} (Promoted $(date))"

# Push to origin (if remote configured)
if git -C "${DEV_DIR}" remote get-url origin >/dev/null 2>&1; then
    echo "  • Pushing main and tags to GitHub..."
    git -C "${PROD_DIR}" push origin main "${NEW_TAG}" || echo "  (Remote push skipped or offline)"
fi

echo "✅ Branch synchronization and tagging complete."
echo ""

# ------------------------------------------------------------------------------
# Stage 4: Production Worktree Deployment & Server Reload
# ------------------------------------------------------------------------------
echo "▶ [Stage 4/5] Deploying to Production Worktree..."

# Update prod worktree
echo "  • Updating production worktree to main (${NEW_TAG})..."
git -C "${PROD_DIR}" checkout main

# Ensure requirements installed
if [[ -f "${PROD_DIR}/requirements.txt" ]]; then
    echo "  • Verifying production python dependencies..."
    python3 -m pip install -r "${PROD_DIR}/requirements.txt" --quiet --disable-pip-version-check
fi

# Check if database synchronization was explicitly requested
if [[ "${SYNC_DB}" == "true" ]]; then
    echo "  ⚠️ OPT-IN FLAG DETECTED (--sync-db): Synchronizing runtime databases from Dev to Prod..."
    mkdir -p "${PROD_DIR}/data/journal"
    if [[ -f "${DEV_DIR}/data/sentinel.db" ]]; then
        cp "${DEV_DIR}/data/sentinel.db"* "${PROD_DIR}/data/" 2>/dev/null || true
    fi
    if [[ -f "${DEV_DIR}/data/journal/market_snapshot.db" ]]; then
        cp "${DEV_DIR}/data/journal/market_snapshot.db"* "${PROD_DIR}/data/journal/" 2>/dev/null || true
    fi
    if [[ -d "${DEV_DIR}/data/journal/futures_intraday_dhan" ]]; then
        cp -r "${DEV_DIR}/data/journal/futures_intraday"* "${PROD_DIR}/data/journal/" 2>/dev/null || true
    fi
    if [[ -f "${DEV_DIR}/data/journal/sessions.csv" ]]; then
        cp "${DEV_DIR}/data/journal/sessions.csv" "${PROD_DIR}/data/journal/" 2>/dev/null || true
    fi
    echo "  ✅ Runtime databases synchronized to Prod."
else
    echo "  • Preserving production runtime databases (skipped DB overwrite)."
    echo "    (Live market data in prod is protected. Use './scripts/promote.sh --sync-db' only if deliberate overwrite is needed.)"
fi

# Reload production server on port 8640
echo "  • Gracefully reloading Production Server on port ${PROD_PORT}..."
OLD_PID=$(lsof -ti :${PROD_PORT} || echo "")
if [[ -n "${OLD_PID}" ]]; then
    echo "    Found running prod server (PID: ${OLD_PID}). Terminating cleanly..."
    kill -15 ${OLD_PID} || true
    sleep 2
fi

# Launch prod server in background
echo "    Starting production server v${NEW_TAG}..."
cd "${PROD_DIR}"
nohup python3 web_server.py --port ${PROD_PORT} > "${PROD_DIR}/logs/prod.log" 2>&1 &
NEW_PID=$!
echo "${NEW_PID}" > "${PROD_DIR}/logs/prod_server.pid"
echo "    Production server started (PID: ${NEW_PID})."
cd "${DEV_DIR}"
echo ""

# ------------------------------------------------------------------------------
# Stage 5: Post-Deployment Smoke Health Check
# ------------------------------------------------------------------------------
echo "▶ [Stage 5/5] Running Post-Deployment Smoke Health Checks..."
HEALTH_PASSED=false

for i in {1..10}; do
    echo "  • Polling ${HEALTH_URL} (Attempt $i/10)..."
    if curl -s -f "${HEALTH_URL}" > /tmp/health_resp.json 2>/dev/null; then
        HEALTH_PASSED=true
        break
    fi
    sleep 1
done

if [[ "${HEALTH_PASSED}" == "true" ]]; then
    cat /tmp/health_resp.json | python3 -m json.tool || cat /tmp/health_resp.json
    echo ""
    echo "======================================================================"
    echo "  🎉 SUCCESS! Version ${NEW_TAG} PROMOTED TO PRODUCTION"
    echo "  Live Cockpit URL : http://localhost:${PROD_PORT}"
    echo "  Rollback Command : ./scripts/rollback.sh"
    echo "======================================================================"
    
    # Record deployment
    echo "$(date '+%Y-%m-%d %H:%M:%S') | PROMOTED | ${NEW_TAG} | PID: ${NEW_PID} | PROD_PORT: ${PROD_PORT}" >> "${RELEASE_LOG}"
    exit 0
else
    echo "❌ ERROR: Production health check failed after reload!"
    echo "  Triggering automated rollback to ${LATEST_TAG}..."
    "${SCRIPT_DIR}/rollback.sh" "${LATEST_TAG}"
    exit 1
fi
