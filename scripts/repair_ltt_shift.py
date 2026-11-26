"""One-off repair: add back the 5:30 that the old LTT decode subtracted from true-epoch ticks.

Until v5.3.171 the WS collectors assumed Dhan's LTT was IST wall time and subtracted 19,800 s. On
the days Dhan sent a true epoch, every stored blast tick and big-money event landed 5:30 early
(a 13:00 tick stored as 07:30). This script shifts the named days forward by 19,800 s.

Guards (fail loud):
  * Only the days you name are touched.
  * A day is refused if any row is stamped at or after 10:10 IST. A shifted session ends by
    10:10 (15:40 − 5:30), so a later row means the day is not (or not wholly) shifted.
  * Dry run by default. ``--apply`` writes a timestamped backup next to each file first.

  python scripts/repair_ltt_shift.py --root ../cubera-gex-cockpit-prod \
      --big-money-day 2026-11-20 --big-money-day 2026-11-25 --big-money-day 2026-11-26 \
      --blast-file SENSEX/2026-11-26
  # add --apply once the dry-run counts look right
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SHIFT_S = 19_800
IST = timezone(timedelta(hours=5, minutes=30))
SHIFTED_SESSION_END = "10:10:00"          # 15:40 IST close minus 5:30


class RepairRefused(RuntimeError):
    pass


def _ist(epoch: int) -> datetime:
    return datetime.fromtimestamp(int(epoch), IST)


def _backup(path: Path, stamp: str) -> Path:
    dst = path.with_name(f"{path.name}.bak-ltt-{stamp}")
    shutil.copy2(path, dst)
    return dst


def repair_big_money(db: Path, days: list[str], apply: bool, stamp: str) -> dict[str, int]:
    """Shift every events row on ``days`` by +SHIFT_S. Rewrites ts_ist and day from the new epoch."""
    con = sqlite3.connect(db, timeout=10.0)
    try:
        counts: dict[str, int] = {}
        for day in days:
            n, late = con.execute(
                "SELECT count(*), sum(substr(ts_ist, 12) >= ?) FROM events WHERE day = ?",
                (SHIFTED_SESSION_END, day)).fetchone()
            if not n:
                raise RepairRefused(f"big_money {day}: no rows")
            if late:
                raise RepairRefused(f"big_money {day}: {late} rows at/after {SHIFTED_SESSION_END}, not a shifted day")
            counts[day] = n
        if not apply:
            return counts
        con.execute("PRAGMA wal_checkpoint(FULL)")
        _backup(db, stamp)
        cols = [r[1] for r in con.execute("PRAGMA table_info(events)")]
        col_list = ", ".join(cols)
        with con:
            for day in days:
                rows = [dict(zip(cols, r)) for r in
                        con.execute(f"SELECT {col_list} FROM events WHERE day = ?", (day,))]
                con.execute("DELETE FROM events WHERE day = ?", (day,))
                for r in rows:
                    r["epoch"] = int(r["epoch"]) + SHIFT_S
                    dt = _ist(r["epoch"])
                    r["ts_ist"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                    r["day"] = dt.strftime("%Y-%m-%d")
                con.executemany(
                    f"INSERT INTO events ({col_list}) VALUES ({', '.join('?' for _ in cols)})",
                    [tuple(r[c] for c in cols) for r in rows])
        return counts
    finally:
        con.close()


def repair_blast_csv(path: Path, apply: bool, stamp: str) -> int:
    """Shift every row of one blast-tick CSV by +SHIFT_S and rewrite time_ist."""
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if not rows:
        raise RepairRefused(f"{path}: no rows")
    late = sum(1 for r in rows if r["time_ist"] >= SHIFTED_SESSION_END)
    if late:
        raise RepairRefused(f"{path}: {late} rows at/after {SHIFTED_SESSION_END}, not a shifted file")
    if not apply:
        return len(rows)
    day = path.stem
    for r in rows:
        r["epoch"] = str(int(r["epoch"]) + SHIFT_S)
        dt = _ist(int(r["epoch"]))
        if dt.strftime("%Y-%m-%d") != day:
            raise RepairRefused(f"{path}: shifted row lands on {dt.date()}, not {day}")
        r["time_ist"] = dt.strftime("%H:%M:%S")
    _backup(path, stamp)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--big-money-day", action="append", default=[])
    ap.add_argument("--blast-file", action="append", default=[], help="SYMBOL/YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    journal = a.root / "data" / "journal"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    mode = "APPLY" if a.apply else "DRY RUN"
    try:
        if a.big_money_day:
            db = journal / "big_money.db"
            for day, n in repair_big_money(db, a.big_money_day, a.apply, stamp).items():
                print(f"[{mode}] big_money {day}: {n} rows +{SHIFT_S}s")
        for spec in a.blast_file:
            p = journal / "blast_ticks" / f"{spec}.csv"
            print(f"[{mode}] blast_ticks {spec}: {repair_blast_csv(p, a.apply, stamp)} rows +{SHIFT_S}s")
    except RepairRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
