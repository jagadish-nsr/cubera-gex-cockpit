#!/usr/bin/env python3
"""Mark scored premarket paths as NO_BOOK so they leave the same-regime base rate.

25 Nov: Nifty and Bank Nifty were discovered against the 23 Nov expiry-day close
(net GEX exactly 0, flip equal to spot, one placeholder strike) and scored 96.2%.
Those scores are not evidence about any regime. This sets thesis_outcome to NO_BOOK
and clears path_efficiency_pct (regime_path_stats ignores rows with no efficiency).
The original outcome, efficiency and detail are kept under "pre_correction".

Dry run by default. Nothing else in the row changes.

    python3 scripts/mark_path_no_book.py --date 2026-11-25 --symbols NIFTY BANKNIFTY
    python3 scripts/mark_path_no_book.py --date 2026-11-25 --symbols NIFTY BANKNIFTY --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path("data/journal/market_snapshot.db")
REASON = "no usable book at discovery (stored close was an expired chain)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="trade_date, YYYY-MM-DD")
    ap.add_argument("--symbols", nargs="+", required=True, help="NIFTY BANKNIFTY SENSEX")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = ap.parse_args(argv)

    if not args.db.is_file():
        print(f"no database at {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db, timeout=10.0)
    conn.row_factory = sqlite3.Row
    todo = []
    for sym in (s.upper() for s in args.symbols):
        row = conn.execute(
            "SELECT trade_date, symbol, gap_regime, thesis_outcome, path_efficiency_pct, "
            "path_score_detail_json FROM market_auctions_history WHERE trade_date = ? AND symbol = ?",
            (args.date, sym),
        ).fetchone()
        if row is None:
            print(f"{sym} {args.date}: no auction row")
            continue
        if row["thesis_outcome"] == "NO_BOOK":
            print(f"{sym} {args.date}: already NO_BOOK")
            continue
        print(
            f"{sym} {args.date}: regime={row['gap_regime']} outcome={row['thesis_outcome']} "
            f"eff={row['path_efficiency_pct']} -> NO_BOOK, eff cleared"
        )
        todo.append(row)

    if not todo:
        return 0
    if not args.apply:
        print("dry run: nothing written (add --apply)")
        return 0

    backup = args.db.with_name(f"{args.db.name}.{datetime.now():%Y%m%d%H%M%S}.bak")
    shutil.copy2(args.db, backup)
    print(f"backup: {backup}")
    for row in todo:
        try:
            detail = json.loads(row["path_score_detail_json"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        detail = {
            **detail,
            "unscored": REASON,
            "pre_correction": {
                "thesis_outcome": row["thesis_outcome"],
                "path_efficiency_pct": row["path_efficiency_pct"],
            },
        }
        conn.execute(
            "UPDATE market_auctions_history SET thesis_outcome = 'NO_BOOK', "
            "path_efficiency_pct = NULL, path_score_detail_json = ? "
            "WHERE trade_date = ? AND symbol = ?",
            (json.dumps(detail), row["trade_date"], row["symbol"]),
        )
    conn.commit()
    print(f"updated {len(todo)} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
