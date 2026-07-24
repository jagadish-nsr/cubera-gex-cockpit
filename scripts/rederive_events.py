"""Re-derive big-money EVENTS from the captured RAW tick archive through the CURRENT detector — so a
detector improvement (e.g. the fixed conviction window) applies to already-captured days. Raw is the
source of truth; this recomputes prints/bursts/ΔOI/conviction from it.

By default writes to a SEPARATE DB (data/journal/big_money_rederived.db) so the live store is untouched
— point the analysis at it with `--db`. Use `--replace` to delete + rewrite the day IN the live DB.

  python scripts/rederive_events.py 2026-07-24                       # all symbols → rederived.db
  python scripts/rederive_events.py 2026-07-24 NIFTY --db /tmp/x.db
  python scripts/rederive_events.py 2026-07-24 --replace              # repair the live DB for that day
  python scripts/big_money_analysis.py 2026-07-24 --db data/journal/big_money_rederived.db
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

from app.big_money_live import DEFAULT_SYMBOLS, rederive_events    # noqa: E402


def _print(s: str = "") -> None:
    print(s, flush=True)


def main() -> None:
    import app.big_money_db as bmdb
    ap = argparse.ArgumentParser()
    ap.add_argument("args", nargs="*")
    ap.add_argument("--db", default=str(ROOT / "data" / "journal" / "big_money_rederived.db"))
    ap.add_argument("--replace", action="store_true",
                    help="delete + rewrite the day in the LIVE DB (repair), instead of a separate DB")
    ns = ap.parse_args()
    day = next((a for a in ns.args if a.count("-") == 2), None)
    if not day:
        _print("Give a day YYYY-MM-DD."); return
    syms = [a.upper() for a in ns.args if a.count("-") != 2] or list(DEFAULT_SYMBOLS)

    target = bmdb.DB_PATH if ns.replace else Path(ns.db)
    bmdb.DB_PATH = target
    _print(f"Re-deriving {day} for {syms} → {target.name}{'  (LIVE, --replace)' if ns.replace else ''}")
    for sym in syms:
        if ns.replace:
            with bmdb._connect() as con:
                con.execute("DELETE FROM events WHERE symbol=? AND day=?", (sym, day))
        evs = rederive_events(sym, day)
        for e in evs:
            bmdb.insert_event(e)
        _print(f"  {sym:<10} {len(evs)} events re-derived from raw")
    _print(f"done → {target}")


if __name__ == "__main__":
    main()
