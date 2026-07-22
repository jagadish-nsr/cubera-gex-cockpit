"""Big-money EVENT rollup ([W2]) — the day-by-day directional history from the SQLite event store.

Per (day, symbol): event / print / burst counts, buy- vs sell-initiated notional, net flow + lean.
This is the "which way big money leaned" ledger — descriptive order flow, not a validated forecast.

  python scripts/big_money_rollup.py                 # every day on record
  python scripts/big_money_rollup.py 2026-07-23       # one day
  python scripts/big_money_rollup.py --symbols NIFTY  # filter
  python scripts/big_money_rollup.py --migrate         # import legacy JSONL into the DB, then show
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.big_money_db import DB_PATH, daily_rollup, migrate_jsonl    # noqa: E402


def _print(s: str = "") -> None:
    print(s, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("days", nargs="*", help="specific day(s) YYYY-MM-DD (default: all on record)")
    ap.add_argument("--symbols", default=None, help="comma-separated filter, e.g. NIFTY,BANKNIFTY")
    ap.add_argument("--migrate", action="store_true", help="import legacy JSONL events first")
    args = ap.parse_args()

    if args.migrate:
        n = migrate_jsonl()
        _print(f"migrated {n} legacy JSONL event line(s) into {DB_PATH.name}\n")

    syms = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    rows = daily_rollup(days=args.days or None, symbols=syms)
    if not rows:
        _print("No big-money events on record yet. (Run the collector during market hours.)"); return

    _print(f"Big-money event rollup · {DB_PATH}")
    _print(f"  {'day':<11}{'symbol':<11}{'evt':>4}{'P/B':>8}  {'buy₹cr':>8}{'sell₹cr':>9}"
           f"{'net₹cr':>9}  {'lean':<7}{'biggest':>9}")
    arrow = {"BUY": "▲", "SELL": "▼", "flat": "•"}
    for r in rows:
        pb = f"{r['n_print']}/{r['n_burst']}"
        lean = arrow[r["lean"]] + r["lean"]
        _print(f"  {r['day']:<11}{r['symbol']:<11}{r['n']:>4}{pb:>8}  "
               f"{r['buy_notional']/1e7:>8.2f}{r['sell_notional']/1e7:>9.2f}"
               f"{r['net_notional']/1e7:>+9.2f}  {lean:<7}{r['biggest']/1e7:>8.2f}")
    _print("\n  Net flow = buy-initiated − sell-initiated ₹ (Lee-Ready). Descriptive order-flow lean, "
           "NOT a validated forecast. Sign-free; decision-support.")


if __name__ == "__main__":
    main()
