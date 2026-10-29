#!/usr/bin/env bash
# One Dhan index-futures tape for this checkout. Waits for 09:15 IST and stops at the F&O close.
# Prod cron starts this at 09:10 on weekdays. A second start leaves the live process alone.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
pidfile="data/journal/big_money.pid"
if [[ -f "$pidfile" ]]; then
    old="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
        echo "big-money collector already running (pid $old)" >> data/journal/big_money_cron.log
        exit 0
    fi
fi
echo $$ > "$pidfile"
trap 'rm -f "$pidfile"' EXIT
python3 scripts/run_big_money_collector.py --market-hours >> data/journal/big_money_cron.log 2>&1
