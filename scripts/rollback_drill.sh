#!/usr/bin/env bash
# ==============================================================================
# Quarterly rollback drill (Pillar H3)
#
#   ./scripts/rollback_drill.sh              # dry-run checklist + log
#   ./scripts/rollback_drill.sh --execute    # timed rollback.sh + health schema
#   ./scripts/rollback_drill.sh --execute --force   # allow during cash session
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROD_PORT=8640
HEALTH_URL="http://localhost:${PROD_PORT}/api/health"
DRILL_LOG="${DEV_DIR}/logs/rollback_drills.log"
MODE="dry-run"
FORCE=0
TARGET_TAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) MODE="execute"; shift ;;
    --force) FORCE=1; shift ;;
    --tag) TARGET_TAG="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$(dirname "${DRILL_LOG}")"

if [[ -z "${TARGET_TAG}" ]]; then
  TARGET_TAG=$(git -C "${DEV_DIR}" tag -l "v*" --sort=-v:refname | sed -n '2p')
fi
CURRENT_TAG=$(git -C "${DEV_DIR}" describe --tags --abbrev=0 2>/dev/null || echo "unknown")

echo "======================================================================"
echo "  ROLLBACK DRILL — mode=${MODE}"
echo "  Current tip tag : ${CURRENT_TAG}"
echo "  Rollback target : ${TARGET_TAG:-"(none found)"}"
echo "======================================================================"

if [[ -z "${TARGET_TAG}" ]]; then
  echo "ERROR: no previous v* tag to roll back to." >&2
  exit 1
fi

echo ""
echo "Checklist:"
echo "  1. Prefer market-closed window (or --force)."
echo "  2. curl ${HEALTH_URL} — note version + readiness before change."
echo "  3. ./scripts/rollback.sh ${TARGET_TAG}"
echo "  4. Assert health version + readiness.ready/degrade_mode/probes.ingest."
echo "  5. Record elapsed seconds in session eval; re-promote when ready."
echo "  6. If dual ingest is required, run_prod.sh / promote after rollback."
echo ""

_ist_minutes() {
  python3 - <<'PY'
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
except Exception:
    from datetime import timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
print(now.hour * 60 + now.minute)
PY
}

_cash_session_open() {
  local mins
  mins="$(_ist_minutes)"
  # 09:15–15:30 IST
  [[ "${mins}" -ge $((9 * 60 + 15)) && "${mins}" -le $((15 * 60 + 30)) ]]
}

_assert_health_schema() {
  local expected_tag="$1"
  local tag_bare="${expected_tag#v}"
  python3 - <<PY
import json, sys, urllib.request
url = "${HEALTH_URL}"
with urllib.request.urlopen(url, timeout=5) as resp:
    data = json.load(resp)
ver = str(data.get("version") or "")
tag = "${tag_bare}"
if ver.replace("v", "") != tag and not ver.startswith(tag):
    print(f"ERROR: health version {ver!r} != rollback target {tag!r}", file=sys.stderr)
    sys.exit(1)
rd = data.get("readiness")
if not isinstance(rd, dict):
    print("ERROR: missing readiness object", file=sys.stderr)
    sys.exit(1)
for key in ("ready", "degrade_mode", "probes"):
    if key not in rd:
        print(f"ERROR: readiness missing {key!r}", file=sys.stderr)
        sys.exit(1)
if "ingest" not in (rd.get("probes") or {}):
    print("ERROR: readiness.probes.ingest missing", file=sys.stderr)
    sys.exit(1)
print(f"schema OK version={ver} ready={rd.get('ready')} degrade_mode={rd.get('degrade_mode')!r}")
PY
}

_log_line() {
  local elapsed="$1" ok="$2"
  echo "$(date '+%Y-%m-%d %H:%M:%S') | ROLLBACK_DRILL | mode=${MODE} | target=${TARGET_TAG} | elapsed_s=${elapsed} | ok=${ok}" \
    >> "${DRILL_LOG}"
}

if [[ "${MODE}" == "dry-run" ]]; then
  echo "Dry-run only — no prod checkout/restart."
  _log_line "0" "dry-run"
  echo "Logged → ${DRILL_LOG}"
  exit 0
fi

if _cash_session_open && [[ "${FORCE}" -ne 1 ]]; then
  echo "ERROR: cash session appears open (09:15–15:30 IST). Re-run with --force if intentional." >&2
  exit 1
fi

echo -n "Type YES to execute rollback to ${TARGET_TAG}: "
read -r CONFIRM
if [[ "${CONFIRM}" != "YES" ]]; then
  echo "Aborted."
  exit 1
fi

START=$(date +%s)
"${SCRIPT_DIR}/rollback.sh" "${TARGET_TAG}"
OK=0
for i in $(seq 1 30); do
  if _assert_health_schema "${TARGET_TAG}"; then
    OK=1
    break
  fi
  sleep 1
done
END=$(date +%s)
ELAPSED=$((END - START))
if [[ "${OK}" -ne 1 ]]; then
  _log_line "${ELAPSED}" "fail"
  echo "FAIL: health schema not OK after ${ELAPSED}s" >&2
  exit 1
fi
_log_line "${ELAPSED}" "pass"
echo "PASS: rollback drill elapsed_s=${ELAPSED} (target ≤ 300)"
if [[ "${ELAPSED}" -gt 300 ]]; then
  echo "WARNING: exceeded 5-minute target — note in session eval." >&2
fi
