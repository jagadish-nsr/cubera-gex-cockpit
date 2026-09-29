"""Compact finished raw-tick CSVs into Parquet ([W2]) — columnar, ~5-10x smaller, faster scans.

Walks data/journal/big_money_raw/<SYM>/*.csv and compacts each day STRICTLY BEFORE today (IST) into
<day>.parquet, removing the CSV. Idempotent and safe (merges if a Parquet already exists). Today's CSV
is left alone (the collector may still be appending). Readers (load_raw_ticks, big_money_summary)
prefer the Parquet automatically.

  python scripts/compact_raw_ticks.py             # compact all finished days
  python scripts/compact_raw_ticks.py --keep-csv  # keep the CSV alongside the Parquet
  python scripts/compact_raw_ticks.py --today      # also compact today (use only after 15:30 IST)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.big_money import IST                                 # noqa: E402
from app.big_money_live import RAW_DIR, compact_raw_day       # noqa: E402


def _print(s: str = "") -> None:
    print(s, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-csv", action="store_true", help="keep the CSV alongside the Parquet")
    ap.add_argument("--today", action="store_true", help="also compact today (only after 15:30 IST)")
    args = ap.parse_args()

    today = datetime.now(IST).strftime("%Y-%m-%d")
    n_files = n_rows = 0
    for csv in sorted(RAW_DIR.glob("*/*.csv")):
        symbol, day = csv.parent.name, csv.stem
        if day > today or (day == today and not args.today):
            continue
        rows = compact_raw_day(symbol, day, keep_csv=args.keep_csv)
        if rows:
            n_files += 1
            n_rows += rows
            _print(f"  compacted {symbol}/{day}: {rows:,} rows → {day}.parquet"
                   f"{' (kept CSV)' if args.keep_csv else ''}")
    _print(f"done — {n_files} day-file(s), {n_rows:,} rows compacted." if n_files
           else "nothing to compact (no finished CSVs).")


if __name__ == "__main__":
    main()
