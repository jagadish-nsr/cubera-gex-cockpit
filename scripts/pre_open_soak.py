#!/usr/bin/env python3
"""Pre-open soak watcher (Pillar G3).

Polls a dual-process serve URL for SOAK_MINUTES (default 30). Fails loud on:
- health unreachable
- version change mid-soak (unexpected restart without promote)
- readiness ready=false while market_open (burn)
- watchdog.restart ops event if Admin ops is reachable

Usage:
  python3 scripts/pre_open_soak.py --base http://127.0.0.1:8640 --minutes 30
  SOAK_MINUTES=5 python3 scripts/pre_open_soak.py   # short dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


def _get(url: str, timeout: float = 8.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("COCKPIT_SOAK_BASE", "http://127.0.0.1:8640"))
    parser.add_argument(
        "--minutes",
        type=float,
        default=float(os.environ.get("SOAK_MINUTES", "30")),
        help="Soak duration (use 1–5 for a dry run)",
    )
    parser.add_argument("--interval", type=float, default=30.0, help="Poll interval seconds")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    end = time.time() + max(0.1, args.minutes) * 60.0
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"soak start {started} base={base} minutes={args.minutes}")

    try:
        first = _get(f"{base}/api/health")
    except urllib.error.URLError as exc:
        print(f"SOAK FAIL: health unreachable: {exc}", file=sys.stderr)
        return 2

    version0 = first.get("version")
    ready_false = 0
    polls = 0
    watchdog = 0

    while time.time() < end:
        polls += 1
        try:
            health = _get(f"{base}/api/health")
        except urllib.error.URLError as exc:
            print(f"SOAK FAIL: health drop at poll={polls}: {exc}", file=sys.stderr)
            return 1
        ver = health.get("version")
        if ver != version0:
            print(f"SOAK FAIL: version changed {version0} → {ver} (unexpected restart)", file=sys.stderr)
            return 1
        readiness = health.get("readiness") or {}
        if readiness.get("market_open") and not readiness.get("ready"):
            ready_false += 1
            print(f"  warn poll={polls} ready=false reasons={readiness.get('reasons')}")
        try:
            ops = _get(f"{base}/api/admin/ops?window=15m&limit=20")
            fails = ((ops.get("events_summary") or {}).get("failures") or {})
            watchdog = max(watchdog, int(fails.get("watchdog.restart") or 0))
        except Exception:
            pass
        time.sleep(max(1.0, args.interval))

    if watchdog > 0:
        print(f"SOAK FAIL: watchdog.restart count={watchdog}", file=sys.stderr)
        return 1
    # Allow a few not-ready minutes early; burn > 10% of polls is fail for soak.
    burn = ready_false / max(1, polls)
    print(f"soak done polls={polls} ready_false={ready_false} burn={burn:.2%} version={version0}")
    if burn > 0.10:
        print("SOAK FAIL: readiness burn > 10%", file=sys.stderr)
        return 1
    print("SOAK PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
