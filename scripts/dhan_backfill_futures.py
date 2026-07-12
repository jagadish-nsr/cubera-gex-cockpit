"""Backfill 1-min intraday index-FUTURES (OHLC + volume + OI) from Dhan for NIFTY / BANKNIFTY.

Unlike the options backfill, Dhan only serves the last 90 days of intraday AND only the active
(near-month) contract — expired contracts drop out of the scrip master — so futures depth is
inherently shallow (~one contract's life). We grab everything available for the current
near-month, one parquet per trading day under data/journal/dhan_futures/<SYMBOL>/.

  python scripts/dhan_backfill_futures.py                 # NIFTY + BANKNIFTY, last 90d
  python scripts/dhan_backfill_futures.py NIFTY           # one symbol

Needs the Dhan token (env DHAN_ACCESS_TOKEN or data/.dhan_token). Journal is gitignored.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.dhan import backfill_futures_intraday  # noqa: E402


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    symbols = [sys.argv[1]] if len(sys.argv) > 1 else ["NIFTY", "BANKNIFTY"]
    _print("Dhan intraday FUTURES backfill (near-month, 1-min, ≤90 days)\n")
    for sym in symbols:
        _print(f"{sym}:")
        backfill_futures_intraday(sym, log=_print)
        _print("")
    _print("✅ done")


if __name__ == "__main__":
    main()
