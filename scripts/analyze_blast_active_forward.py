#!/usr/bin/env python3
"""Join first-print ACTIVE blast episodes to forward spot / option paths.

Uses ``gamma_blast_score_minute`` (collapsed) so multi-writer score pollution
cannot invent ACTIVE minutes. Optional ``blast_ticks`` for CE/PE LTP at the
primary strike. Map + risk research only — not a direction model.

  python3 scripts/analyze_blast_active_forward.py
  python3 scripts/analyze_blast_active_forward.py --symbol NIFTY --day 2026-11-09 \\
      --ticks-root ../cubera-gex-cockpit-prod/data/journal/blast_ticks
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.blast_forward import join_active_forward, load_minute_grid  # noqa: E402

DEFAULTS = (
    ("SENSEX", "2026-11-05"),
    ("NIFTY", "2026-11-09"),
)


def _load_ticks(root: Path, symbol: str, day: str) -> list[dict]:
    path = Path(root) / symbol.upper() / f"{day}.csv"
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol")
    ap.add_argument("--day")
    ap.add_argument(
        "--minute-root",
        type=Path,
        default=ROOT / "data" / "journal" / "gamma_blast_score_minute",
    )
    ap.add_argument(
        "--ticks-root",
        type=Path,
        default=ROOT / "data" / "journal" / "blast_ticks",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "data" / "journal" / "blast_active_forward",
    )
    args = ap.parse_args()
    jobs = list(DEFAULTS)
    if args.symbol or args.day:
        if not (args.symbol and args.day):
            ap.error("--symbol and --day together")
        jobs = [(args.symbol.upper(), args.day)]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    wrote = 0
    for sym, day in jobs:
        grid = args.minute_root / sym / f"{day}.csv"
        if not grid.exists():
            print(f"MISSING minute grid {grid}")
            continue
        rows = load_minute_grid(grid)
        ticks = _load_ticks(args.ticks_root, sym, day)
        joined = join_active_forward(rows, ticks or None)
        out = args.out_dir / f"{sym}_{day}.csv"
        if not joined:
            print(f"NO_ACTIVE {sym} {day}")
            continue
        fields = []
        for row in joined:
            for key in row:
                if key not in fields:
                    fields.append(key)
        with out.open("w", newline="", encoding="utf-8") as handle:
            w = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(joined)
        # Console summary
        print(f"\n=== {sym} {day}  episodes={len(joined)}  ticks={len(ticks)} ===")
        print(
            f"{'start':>5} {'end':>5} {'n':>3} {'sc':>3} "
            f"{'d5':>7} {'d15':>7} {'d30':>7} {'mfe15':>7} {'mae15':>7} "
            f"{'side':>5} {'ce15%':>7} {'pe15%':>7}"
        )
        for r in joined:
            ce = "" if r.get("ce_ret") is None else f"{100 * r['ce_ret']:+.1f}"
            pe = "" if r.get("pe_ret") is None else f"{100 * r['pe_ret']:+.1f}"
            print(
                f"{r['start']:>5} {r['end']:>5} {r['n_minutes']:>3} {r.get('score') or '':>3} "
                f"{r.get('d5') if r.get('d5') is not None else '':>7} "
                f"{r.get('d15') if r.get('d15') is not None else '':>7} "
                f"{r.get('d30') if r.get('d30') is not None else '':>7} "
                f"{r.get('mfe15') if r.get('mfe15') is not None else '':>7} "
                f"{r.get('mae15') if r.get('mae15') is not None else '':>7} "
                f"{(r.get('vs_primary') or ''):>5} {ce:>7} {pe:>7}"
            )
        print(f"wrote {out}")
        wrote += 1
    return 0 if wrote else 1


if __name__ == "__main__":
    raise SystemExit(main())
