"""Phase A capture loop — append an intraday Nifty GEX snapshot to the day's
time-series (``data/journal/timeseries/YYYY-MM-DD.csv``, gitignored).

  python scripts/log_timeseries.py             # loop every 15 min during NSE hours
  python scripts/log_timeseries.py --once       # one snapshot, then exit (for schedulers)
  python scripts/log_timeseries.py --loop 5     # custom interval (minutes)
  python scripts/log_timeseries.py --once --ignore-session   # capture off-hours (testing)

Reuses the dashboard pipeline (ONE NSE fetch -> levels + OI/volume aggregates), so
the engine stays the single source of truth. A failed pull is logged and the loop
keeps going (fail loud per row, don't crash the day). Decision-support only.

NOTE: the session guard does NOT know NSE holidays — on a holiday it will idle
during 09:15-15:30 IST and record flat/stale data (the OI staleness flag covers
this). Add the holiday calendar later if it matters.
"""

from __future__ import annotations

import argparse
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data_access import load_dashboard  # noqa: E402
from app.timeseries import append_if_due, build_row  # noqa: E402

_OPEN_IST = (9, 15)    # NSE session 09:15-15:30 IST
_CLOSE_IST = (15, 30)


def _ist_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _in_session(ist: datetime) -> bool:
    if ist.weekday() >= 5:  # Sat / Sun
        return False
    o = ist.replace(hour=_OPEN_IST[0], minute=_OPEN_IST[1], second=0, microsecond=0)
    c = ist.replace(hour=_CLOSE_IST[0], minute=_CLOSE_IST[1], second=0, microsecond=0)
    return o <= ist <= c


def capture_once(min_gap: float = 10.0) -> str:
    data = load_dashboard()  # live NSE, single fetch
    row = build_row(data)
    path, written = append_if_due(row, min_gap_minutes=min_gap)
    head = (f"{row['date_ist']} {row['time_ist']} IST  spot {row['spot']:,}  "
            f"{row['regime']}/{row['mode_label']}  "
            f"walls {row['put_wall']:,}-{row['call_wall']:,}  magnet {row['magnet']:,}  "
            f"PCR {row['pcr_oi']}")
    if not written:
        return f"{head}  -> skipped (a row < {min_gap:g} min old exists)"
    return f"{head}  -> {path.name}"


def _print(s: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Intraday Nifty GEX time-series capture.")
    ap.add_argument("--once", action="store_true", help="single snapshot then exit")
    ap.add_argument("--loop", type=float, default=15.0, help="interval in minutes (loop mode)")
    ap.add_argument("--min-gap", type=float, default=10.0,
                    help="dedup guard: skip if a row was saved < this many minutes ago "
                         "(auto-clamped below the loop interval; <=0 disables)")
    ap.add_argument("--ignore-session", action="store_true",
                    help="capture even outside NSE hours (testing)")
    args = ap.parse_args()

    if args.once:
        ist = _ist_now()
        if not args.ignore_session and not _in_session(ist):
            _print(f"{ist:%Y-%m-%d %H:%M} IST  outside NSE session - skipped")
            return                                  # clean exit: weekend / off-window fire
        try:
            _print(capture_once(min_gap=args.min_gap))
        except Exception as e:  # noqa: BLE001 - surface and exit non-zero for schedulers
            _print(f"capture FAILED: {e}")
            sys.exit(1)
        return

    # Never let the guard block the loop's own cadence: keep it below the interval.
    gap = min(args.min_gap, max(1.0, args.loop - 1.0)) if args.min_gap > 0 else 0.0
    interval = max(args.loop, 1.0) * 60.0
    _print(f"Time-series capture: every {args.loop:g} min during NSE hours "
           f"(09:15-15:30 IST). Ctrl+C to stop.")
    try:
        while True:
            ist = _ist_now()
            if args.ignore_session or _in_session(ist):
                try:
                    _print(capture_once(min_gap=gap))
                except Exception as e:  # noqa: BLE001 - one bad pull must not stop the day
                    _print(f"{ist:%H:%M} IST  capture FAILED (kept looping): {e}")
            else:
                _print(f"{ist:%Y-%m-%d %H:%M} IST  outside NSE session - idle")
            _time.sleep(interval)
    except KeyboardInterrupt:
        _print("stopped.")


if __name__ == "__main__":
    main()
