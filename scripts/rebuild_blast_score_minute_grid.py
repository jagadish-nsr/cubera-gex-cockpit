#!/usr/bin/env python3
"""Collapse polluted gamma_blast_score CSVs to one first-print row per IST minute.

Usage:
  python3 scripts/rebuild_blast_score_minute_grid.py
  python3 scripts/rebuild_blast_score_minute_grid.py --symbol NIFTY --day 2026-11-09
  python3 scripts/rebuild_blast_score_minute_grid.py --src-root /path/to/prod/data/journal/gamma_blast_score

Default days: Sensex 2026-11-05 and Nifty 2026-11-09 (the two expiry audits).
Writes ``data/journal/gamma_blast_score_minute/<SYM>/<day>.csv``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.gammablast import write_blast_score_minute_grid  # noqa: E402

DEFAULTS = (
    ("SENSEX", "2026-11-05"),
    ("NIFTY", "2026-11-09"),
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", help="Single symbol (default: both audit days)")
    ap.add_argument("--day", help="YYYY-MM-DD (requires --symbol)")
    ap.add_argument(
        "--src-root",
        type=Path,
        default=None,
        help="gamma_blast_score root (default: repo data/journal/gamma_blast_score)",
    )
    ap.add_argument(
        "--dst-root",
        type=Path,
        default=None,
        help="Output root (default: repo data/journal/gamma_blast_score_minute)",
    )
    args = ap.parse_args()

    jobs = list(DEFAULTS)
    if args.symbol or args.day:
        if not (args.symbol and args.day):
            ap.error("--symbol and --day must be passed together")
        jobs = [(args.symbol.upper(), args.day)]

    ok = 0
    for sym, day in jobs:
        out = write_blast_score_minute_grid(
            sym, day, src_dir=args.src_root, dst_dir=args.dst_root,
        )
        if out is None:
            print(f"MISSING {sym} {day}")
            continue
        # Quick stats
        import csv
        rows = list(csv.DictReader(out.open()))
        states = {}
        for r in rows:
            states[r.get("state")] = states.get(r.get("state"), 0) + 1
        print(f"OK {sym} {day} -> {out} rows={len(rows)} states={states}")
        ok += 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
