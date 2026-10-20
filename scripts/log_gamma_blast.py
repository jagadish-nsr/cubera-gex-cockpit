"""Gamma-blast 1-min capture — EXPIRY DAY only, 13:00-15:30 IST.

Captures per-strike OI/ΔOI + nearest-expiry premiums + gamma exposure every
minute through the blast window, into data\\journal\\gamma_blast\\YYYY-MM-DD.csv
(same schema as the daily oi_strikes capture, finer cadence). This is the fine-
grained record for studying whether the ATM call/put premium crossover precedes a
gamma blast on Nifty — the thing the 15-min daily capture is too coarse to see.

  python scripts/log_gamma_blast.py            # loop 1-min during the window (expiry day)
  python scripts/log_gamma_blast.py --once      # one tick if in-window + expiry (schedulers)
  python scripts/log_gamma_blast.py --once --force   # ignore the expiry/window gate (testing)

Reuses the dashboard pipeline (ONE NSE fetch). Exits cleanly off-window, on
weekends, or on a non-expiry day, so a scheduled 1-min fire is a safe no-op.
Decision-support only — no trades.
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
from app.timeseries import (GAMMA_BLAST_DIR, append_strike_rows,  # noqa: E402
                            build_strike_rows)

_WIN_START_IST = (13, 0)     # gamma-blast window 13:00-15:30 IST (afternoon)
_WIN_END_IST = (15, 30)


def _ist_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _in_window(ist: datetime) -> bool:
    try:
        from data.holidays import is_trading_day
        if not is_trading_day(ist.date()):
            return False
    except Exception:
        if ist.weekday() >= 5:
            return False
    m = ist.hour * 60 + ist.minute
    return _WIN_START_IST[0] * 60 + _WIN_START_IST[1] <= m <= _WIN_END_IST[0] * 60 + _WIN_END_IST[1]


def capture_once(force: bool = False, symbol: str | None = None) -> str:
    data = load_dashboard(symbol=symbol)  # live NSE, single fetch
    ist = data.fetched_at + timedelta(hours=5, minutes=30)
    ist_today = ist.date()
    nearest = data.expiries[0] if data.expiries else None
    is_expiry = (nearest == ist_today)
    if not force and not is_expiry:
        return (f"[{data.symbol}] {ist:%Y-%m-%d %H:%M} IST  not an expiry day "
                f"(nearest {nearest}) — skipped")

    rows = build_strike_rows(data)
    if not rows:
        return f"[{data.symbol}] {ist:%Y-%m-%d %H:%M} IST  no per-strike data (live feed only) — skipped"
    from app.timeseries import gamma_blast_dir_for
    path = append_strike_rows(rows, date_ist=ist.strftime("%Y-%m-%d"),
                              symbol=data.symbol, directory=gamma_blast_dir_for(data.source))
    return (f"[{data.symbol}] {ist:%Y-%m-%d} {ist:%H:%M} IST  spot {data.levels.spot:,.1f}  "
            f"magnet {data.levels.magnet:,.0f}  {len(rows)} strikes -> {path.name}")


def _print(s: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Expiry-day 1-min gamma-blast capture.")
    ap.add_argument("--once", action="store_true", help="single tick then exit (schedulers)")
    ap.add_argument("--symbol", default=None,
                    help="index to capture (NIFTY/BANKNIFTY); default NIFTY")
    ap.add_argument("--loop", type=float, default=1.0, help="interval in minutes (loop mode)")
    ap.add_argument("--force", action="store_true",
                    help="ignore the expiry-day / window gate (testing)")
    args = ap.parse_args()

    if args.once:
        ist = _ist_now()
        if not args.force and not _in_window(ist):
            _print(f"{ist:%Y-%m-%d %H:%M} IST  outside 13:00-15:30 window - skipped")
            return
        try:
            _print(capture_once(force=args.force, symbol=args.symbol))
        except Exception as e:  # noqa: BLE001 - surface + non-zero for schedulers
            _print(f"capture FAILED: {e}")
            sys.exit(1)
        return

    interval = max(args.loop, 0.25) * 60.0
    _print(f"Gamma-blast capture: every {args.loop:g} min, 13:00-15:30 IST, expiry day. "
           "Ctrl+C to stop.")
    try:
        while True:
            ist = _ist_now()
            if args.force or _in_window(ist):
                try:
                    _print(capture_once(force=args.force, symbol=args.symbol))
                except Exception as e:  # noqa: BLE001 - one bad pull must not stop the run
                    _print(f"{ist:%H:%M} IST  capture FAILED (kept looping): {e}")
            else:
                _print(f"{ist:%Y-%m-%d %H:%M} IST  outside window - idle")
            _time.sleep(interval)
    except KeyboardInterrupt:
        _print("stopped.")


if __name__ == "__main__":
    main()
