"""Backfill 1-min intraday NIFTY option matrix (ATM±10 CE/PE, WEEK nearest expiry) from Dhan.

Fills the intraday-history gap: real per-strike OI + IV + spot at 1-min, ~5 years back, so the
whole intraday suite (streaks, build-up, gamma-blast, pinning, intraday GEX→vol) can run at a
genuine sample size instead of a handful of live-captured days. Resumable — re-run to extend or
recover; days already on disk are skipped without an API call.

  python scripts/dhan_backfill.py                       # last 5 years → today (NIFTY, weekly)
  python scripts/dhan_backfill.py 2024-01-01 2026-07-11 # explicit range
  python scripts/dhan_backfill.py 2026-01-01 2026-07-11 NIFTY
  python scripts/dhan_backfill.py 2021-07-12 2026-07-11 NIFTY MONTH   # NIFTY monthlies → NIFTY_M

Optional 4th arg = expiry flag (WEEK/MONTH); when it differs from the symbol's default it is
stored under a suffixed key ('NIFTY_M') so the two expiry books never collide. Needs the Dhan
access token (env DHAN_ACCESS_TOKEN or data/.dhan_token, gitignored). Stored to
data/journal/dhan_intraday/<STORE>/<day>.parquet (journal is gitignored — research data only).
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.dhan import DHAN_OPT_FLAG, backfill_intraday_options  # noqa: E402


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    today = date.today()
    start = sys.argv[1] if len(sys.argv) > 1 else (today - timedelta(days=365 * 5)).isoformat()
    end = sys.argv[2] if len(sys.argv) > 2 else today.isoformat()
    symbol = sys.argv[3] if len(sys.argv) > 3 else "NIFTY"

    default_flag = DHAN_OPT_FLAG.get(symbol, "WEEK")
    flag = sys.argv[4].upper() if len(sys.argv) > 4 else default_flag
    store = symbol if flag == default_flag else f"{symbol}_{flag[0]}"   # e.g. NIFTY_M
    _print(f"Dhan intraday option backfill — {symbol} ATM±10 CE/PE, {flag} code1, 1-min "
           f"→ store '{store}'")
    _print(f"  range {start} .. {end}  (newest→oldest, resumable)\n")
    t0 = time.time()
    summary = backfill_intraday_options(start, end, symbol, flag=flag, store=store, log=_print)
    dt = time.time() - t0
    _print(f"\n✅ done in {dt/60:.1f} min — {summary['days_written']} new days, "
           f"{summary['rows_written']:,} rows, {summary['days_on_disk']} days on disk, "
           f"{summary['misses']} series-misses")


if __name__ == "__main__":
    main()
