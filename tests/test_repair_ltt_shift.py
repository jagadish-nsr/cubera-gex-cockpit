"""repair_ltt_shift: +19,800 s on named shifted days, refuses anything that is not wholly shifted."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import big_money_db
from scripts import repair_ltt_shift as r

IST = timezone(timedelta(hours=5, minutes=30))


def _epoch(day: str, hms: str) -> int:
    return int(datetime.strptime(f"{day} {hms}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST).timestamp())


def _seed_db(path, rows):
    con = big_money_db._connect(path)
    with con:
        for day, hms, price in rows:
            con.execute(
                "INSERT INTO events (epoch, ts_ist, day, symbol, kind, notional, price, qty, expiry) "
                "VALUES (?, ?, ?, 'NIFTY', 'print', 1e7, ?, 65, '2026-11-25')",
                (_epoch(day, hms), f"{day} {hms}", day, price))
    con.close()


def _rows(path):
    con = sqlite3.connect(path)
    out = con.execute("SELECT day, ts_ist, epoch, price FROM events ORDER BY epoch").fetchall()
    con.close()
    return out


def test_big_money_shift_by_hand(tmp_path):
    db = tmp_path / "big_money.db"
    # Stored 03:45:02 → true 09:15:02. Stored 09:15:02 → true 14:45:02: both inside the
    # overlap window, so an in-place UPDATE could collide; the rewrite must not.
    _seed_db(db, [("2026-11-25", "03:45:02", 100.0), ("2026-11-25", "09:15:02", 100.0),
                  ("2026-11-24", "11:00:00", 50.0)])
    assert r.repair_big_money(db, ["2026-11-25"], apply=False, stamp="t") == {"2026-11-25": 2}
    assert _rows(db)[1][1] == "2026-11-25 03:45:02"          # dry run wrote nothing

    r.repair_big_money(db, ["2026-11-25"], apply=True, stamp="t")
    got = _rows(db)
    assert got == [
        ("2026-11-24", "2026-11-24 11:00:00", _epoch("2026-11-24", "11:00:00"), 50.0),
        ("2026-11-25", "2026-11-25 09:15:02", _epoch("2026-11-25", "09:15:02"), 100.0),
        ("2026-11-25", "2026-11-25 14:45:02", _epoch("2026-11-25", "14:45:02"), 100.0),
    ]
    assert (tmp_path / "big_money.db.bak-ltt-t").exists()


def test_big_money_refuses_unshifted_day(tmp_path):
    db = tmp_path / "big_money.db"
    _seed_db(db, [("2026-11-19", "09:15:01", 1.0), ("2026-11-19", "15:39:46", 1.0)])
    with pytest.raises(r.RepairRefused, match="not a shifted day"):
        r.repair_big_money(db, ["2026-11-19"], apply=True, stamp="t")
    assert _rows(db)[1][1] == "2026-11-19 15:39:46"


def test_big_money_refuses_missing_day(tmp_path):
    db = tmp_path / "big_money.db"
    _seed_db(db, [("2026-11-19", "09:15:01", 1.0)])
    with pytest.raises(r.RepairRefused, match="no rows"):
        r.repair_big_money(db, ["2026-11-20"], apply=False, stamp="t")


def _write_csv(path, times):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["epoch", "time_ist", "symbol", "ltp"])
        w.writeheader()
        for hms in times:
            w.writerow({"epoch": _epoch(path.stem, hms), "time_ist": hms, "symbol": "SENSEX", "ltp": 12.5})


def test_blast_csv_shift_by_hand(tmp_path):
    p = tmp_path / "SENSEX" / "2026-11-26.csv"
    _write_csv(p, ["07:30:55", "10:09:58"])
    assert r.repair_blast_csv(p, apply=True, stamp="t") == 2
    with p.open() as f:
        rows = list(csv.DictReader(f))
    assert [x["time_ist"] for x in rows] == ["13:00:55", "15:39:58"]
    assert int(rows[0]["epoch"]) == _epoch("2026-11-26", "13:00:55")
    assert rows[0]["ltp"] == "12.5"
    assert p.with_name("2026-11-26.csv.bak-ltt-t").exists()


def test_blast_csv_refuses_correct_file(tmp_path):
    p = tmp_path / "SENSEX" / "2026-11-19.csv"
    _write_csv(p, ["13:00:14", "15:39:59"])
    with pytest.raises(r.RepairRefused):
        r.repair_blast_csv(p, apply=True, stamp="t")
