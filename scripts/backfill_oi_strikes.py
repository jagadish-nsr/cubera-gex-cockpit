"""Backfill the per-strike OI journal for a day (or today) from Dhan's 1-min history.

Fills the gaps in the SEPARATE Dhan journal (data/journal/oi_strikes_dhan/<SYMBOL>/<date>.csv) so
"since open" and the trailing 5/15/30/60-min windows have the whole session even if the dashboard
/logger started late. Native 1-min, idempotent (only writes stamps not already present).

  python scripts/backfill_oi_strikes.py                 # NIFTY, today (IST)
  python scripts/backfill_oi_strikes.py SENSEX          # SENSEX, today
  python scripts/backfill_oi_strikes.py NIFTY 2026-07-17  # a specific day

Needs the Dhan token (data/.dhan_token). Decision-support / research data only.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    from app.oi_backfill import backfill_oi_strikes_dhan

    symbol = (sys.argv[1] if len(sys.argv) > 1 else "NIFTY").upper()
    if len(sys.argv) > 2:
        date_ist = sys.argv[2]
    else:
        date_ist = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    _print(f"Backfill per-strike OI (Dhan, 1-min) — {symbol} {date_ist} -> oi_strikes_dhan/")
    r = backfill_oi_strikes_dhan(symbol, date_ist, log=_print)
    _print(f"done: {r['stamps']} new stamps, {r['rows']} strike-rows ({r['had']} already present)")


if __name__ == "__main__":
    main()
