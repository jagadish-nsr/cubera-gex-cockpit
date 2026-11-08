#!/usr/bin/env bash
# Install weekday data-collection cron for the dev checkout and the prod worktree.
# Each launcher writes only into its own data/journal. Prod runs three minutes
# later so the two trees do not fetch NSE at the same moment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PARENT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
DEV_DIR="${PARENT}/cubera-gex-cockpit"
PROD_DIR="${PARENT}/cubera-gex-cockpit-prod"

if [[ ! -d "${DEV_DIR}" ]]; then
    DEV_DIR="${SCRIPT_DIR}"
fi
if [[ ! -d "${PROD_DIR}" ]]; then
    echo "Production worktree not found at ${PROD_DIR}" >&2
    exit 1
fi

for launcher in run_timeseries.sh run_logger.sh run_participant_oi.sh run_futures_buildup.sh run_big_money.sh run_muhurat_collect.sh run_muhurat_big_money.sh; do
    if [[ ! -x "${DEV_DIR}/${launcher}" ]]; then
        echo "Missing launcher: ${DEV_DIR}/${launcher}" >&2
        exit 1
    fi
    if [[ ! -x "${PROD_DIR}/${launcher}" ]]; then
        echo "Missing launcher: ${PROD_DIR}/${launcher}" >&2
        exit 1
    fi
done

block() {
    local dir="$1"
    local ts="$2"
    local morning="$3"
    local afternoon="$4"
    local eod="$5"
    local label="$6"
    cat <<EOF
# ${label} timeseries, every 15 min from 09:00 through the 15:00 hour IST, Mon-Fri
${ts} 9-15 * * 1-5 ${dir}/run_timeseries.sh
# ${label} session logger, Mon-Fri
${morning} 9 * * 1-5 ${dir}/run_logger.sh
${afternoon} 15 * * 1-5 ${dir}/run_logger.sh
# ${label} participant OI and futures bhavcopy, Mon-Fri
# 18:00 often lands the prior session (NSE late). 21:00 retries same-day.
${eod} 18 * * 1-5 ${dir}/run_participant_oi.sh
${eod} 18 * * 1-5 ${dir}/run_futures_buildup.sh
${eod} 21 * * 1-5 ${dir}/run_participant_oi.sh
${eod} 21 * * 1-5 ${dir}/run_futures_buildup.sh
EOF
}

NEW_BLOCK="$(cat <<EOF
# BEGIN cubera-gex data collection
$(block "${DEV_DIR}" '*/15' 20 0 0 dev)
$(block "${PROD_DIR}" '3,18,33,48' 23 3 3 prod)
# prod big-money futures tape, one process from 09:10 IST, Mon-Fri
10 9 * * 1-5 ${PROD_DIR}/run_big_money.sh
# prod gamma-blast option ticks from 12:55 IST, Mon-Fri (waits for the 13:00 freeze).
# The script skips an index whose nearest expiry is not today and stops at the F&O close.
55 12 * * 1-5 ${PROD_DIR}/run_blast_ticks.sh
# Muhurat special session (any calendar day Laxmi Pujan falls on — weekday or
# weekend). Launchers no-op unless is_muhurat_session() — see muhurat_sessions.json.
*/5 14-15 * * * ${DEV_DIR}/run_muhurat_collect.sh
*/5 14-15 * * * ${PROD_DIR}/run_muhurat_collect.sh
10 14 * * * ${PROD_DIR}/run_muhurat_big_money.sh
# END cubera-gex data collection
EOF
)"

current="$(crontab -l 2>/dev/null || true)"
filtered="$(printf '%s\n' "${current}" | awk '
    /BEGIN cubera-gex data collection/ { skip = 1 }
    skip && /END cubera-gex data collection/ { skip = 0; next }
    skip { next }
    /run_timeseries\.sh/ { next }
    /run_logger\.sh/ { next }
    /run_participant_oi\.sh/ { next }
    /run_futures_buildup\.sh/ { next }
    /run_big_money\.sh/ { next }
    /run_muhurat_collect\.sh/ { next }
    /run_muhurat_big_money\.sh/ { next }
    /run_blast_ticks\.sh/ { next }
    /Indian-GEX Automated Data Collection/ { next }
    /Timeseries snapshots:/ { next }
    /Logger snapshots:/ { next }
    /EOD Tasks:/ { next }
    { print }
')"

{
    printf '%s\n' "${filtered}" | sed '/^[[:space:]]*$/d'
    printf '\n%s\n' "${NEW_BLOCK}"
} | crontab -

echo "Cron jobs installed for:"
echo "  dev  ${DEV_DIR}"
echo "  prod ${PROD_DIR} (three minutes later; big-money tape at 09:10)"
echo "View them with: crontab -l"
