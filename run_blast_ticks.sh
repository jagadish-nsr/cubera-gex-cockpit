#!/usr/bin/env bash
# Expiry-afternoon option-tick capture. Cron fires at 12:55 IST; the script waits for
# the 13:00 freeze and runs through the F&O close. Each index is skipped unless it expires today.
cd "$(dirname "$0")" || exit
mkdir -p data/journal
LOCK="${TMPDIR:-/tmp}/cubera_blast_ticks.lock"
python3 - "$LOCK" <<'PY' >> data/journal/blast_ticks_run.log 2>&1
import fcntl, subprocess, sys, time
from pathlib import Path

lock_path = sys.argv[1]
fd = open(lock_path, "a+", encoding="utf-8")
try:
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "blast_ticks already running — skip", flush=True)
    sys.exit(0)

cmd = [
    sys.executable,
    "scripts/run_blast_ticks.py",
    "--market-hours",
    "--freeze", "13:00",
    "--symbols", "NIFTY,BANKNIFTY,SENSEX",
]
raise SystemExit(subprocess.call(cmd))
PY
