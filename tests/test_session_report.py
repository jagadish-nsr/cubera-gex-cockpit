"""Session report assembly. Offline fixtures only."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from app.session_report import build_session_report, write_session_report
from app.session_report_html import render_session_report


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _init(root: Path) -> None:
    snap = root / "data" / "journal" / "market_snapshot.db"
    snap.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(snap)
    conn.execute(
        """
        CREATE TABLE session_snapshots (
            symbol TEXT, spot REAL, prev_close REAL, open REAL, high REAL, low REAL,
            call_wall REAL, put_wall REAL, gamma_flip REAL, max_pain REAL, net_gex REAL,
            pcr REAL, vix REAL, as_of TEXT, source TEXT, updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE market_auctions_history (
            trade_date TEXT, symbol TEXT, post_market_settled INTEGER,
            settlement_spot REAL, prev_close REAL, net_change_pts REAL,
            net_change_pct REAL, day_range TEXT, discovered_spot REAL, gap_pts REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO session_snapshots VALUES
        ('NIFTY', 22944.95, 22600, 22524.4, 22753.2, 22387.3,
         23000, 22800, 22958.3, 22900, -1.5e10, 0.92, 15.1,
         '2026-10-29T09:45:07+00:00', 'test', '2026-10-29T09:45:08+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO market_auctions_history VALUES
        ('2026-10-29', 'NIFTY', 1, 22944.95, 22698.85, 246.1, 1.08,
         '₹22,753.2 / ₹22,387.3', 22690.65, -8.2)
        """
    )
    conn.commit()
    conn.close()

    rec = root / "data" / "journal" / "recommendations.db"
    conn = sqlite3.connect(rec)
    conn.execute(
        """
        CREATE TABLE recommendation_records (
            triggered_at TEXT, symbol TEXT, side TEXT, status TEXT, play_badge TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE recommendation_events (
            event_ts TEXT, event_type TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO recommendation_records VALUES (?, ?, ?, ?, ?)",
        [
            ("2026-10-29 10:43:22", "NIFTY", "CE", "ACTIVE", "MOMENTUM"),
            ("2026-10-29 10:43:22", "NIFTY", "PE", "ACTIVE", "MOMENTUM"),
            ("2026-10-29 10:43:22", "NIFTY", "CE", "INVALIDATED", "STRIKE BREAK"),
        ],
    )
    conn.execute("INSERT INTO recommendation_events VALUES ('2026-10-29 10:50:00', 'INVALIDATED')")
    conn.commit()
    conn.close()

    comm = root / "data" / "journal" / "commentary.db"
    conn = sqlite3.connect(comm)
    conn.execute(
        "CREATE TABLE commentary_entries (ts TEXT, symbol TEXT, phase TEXT, headline TEXT)"
    )
    conn.execute("CREATE TABLE commentary_bites (ts TEXT, state TEXT)")
    conn.execute("CREATE TABLE commentary_events (ts TEXT)")
    conn.execute(
        """
        INSERT INTO commentary_entries VALUES
        ('2026-10-29T10:10:28+05:30', 'NIFTY', 'morning',
         'Call Wall Migrated from ₹24,600 to ₹23,000')
        """
    )
    conn.commit()
    conn.close()

    sent = root / "data" / "sentinel.db"
    conn = sqlite3.connect(sent)
    conn.execute(
        """
        CREATE TABLE sentinel_signals (
            event_id TEXT, instrument_id TEXT, bar_start_ts TEXT, type TEXT, bias TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE signal_outcomes (
            event_id TEXT, target_hit INTEGER, mfe_pts REAL, mae_pts REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE sentinel_bars (
            instrument_id TEXT, timeframe TEXT, start_ts TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE sentinel_daily_cvd (
            trade_date TEXT, instrument_id TEXT, closing_cvd REAL, closing_price REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sentinel_signals VALUES
        ('e1', 'NIFTY-FUT', '2026-10-29T10:00:00+05:30', 'CONTINUATION_BULL', 'LONG'),
        ('e2', 'NIFTY-FUT', '2026-10-29T10:05:00+05:30', 'CONTINUATION_BULL', 'LONG')
        """
    )
    conn.execute("INSERT INTO signal_outcomes VALUES ('e1', 1, 40.0, 10.0)")
    conn.execute("INSERT INTO signal_outcomes VALUES ('e2', 0, 5.0, 20.0)")
    conn.execute(
        "INSERT INTO sentinel_daily_cvd VALUES ('2026-10-29', 'TEST-FUT', 10, 1)"
    )
    conn.commit()
    conn.close()

    _write_csv(
        root / "data" / "journal" / "timeseries" / "NIFTY" / "2026-10-29.csv",
        [
            {
                "time_ist": "09:15", "spot": "22690.65", "regime": "POSITIVE",
                "mode_label": "CALL-HEAVY", "flip": "22671.6", "call_wall": "23000",
                "put_wall": "22500", "magnet": "22700", "max_pain": "22700",
                "net_gex": "100", "pcr_oi": "1.07", "india_vix": "16.25",
                "vix_pct_chg": "-0.96", "stale": "False",
            },
            {
                "time_ist": "15:15", "spot": "22944.95", "regime": "NEGATIVE",
                "mode_label": "PUT-HEAVY", "flip": "22958.3", "call_wall": "23000",
                "put_wall": "22800", "magnet": "23000", "max_pain": "22900",
                "net_gex": "-15106200507", "pcr_oi": "1.312", "india_vix": "15.12",
                "vix_pct_chg": "-6.9", "stale": "False",
            },
        ],
    )


def test_report_uses_samples_and_refuses_a_range_that_misses_the_settle(tmp_path: Path):
    _init(tmp_path)
    doc = build_session_report("2026-10-29", tmp_path)

    cash = doc["cash_path"]
    assert cash["intervals"] == 2
    assert cash["open_spot"] == 22690.65
    assert cash["close_spot"] == 22944.95
    assert cash["sample_change_pts"] == 254.3
    assert cash["regime_flip_time"] == "15:15"
    assert cash["regime_flip_to"] == "NEGATIVE"

    nifty = next(row for row in doc["indices"] if row["symbol"] == "NIFTY")
    assert nifty["settled"] is True
    assert nifty["net_change_pts"] == 246.1
    assert nifty["auction_range_used"] is False
    assert nifty["range_used"] is False
    assert "does not contain the settlement print" in nifty["range_note"]

    assert doc["recommendations"]["total"] == 3
    assert doc["recommendations"]["by_status"]["INVALIDATED"] == 1
    assert doc["commentary"]["entries"] == 1
    assert any("24,600" in note for note in doc["commentary"]["headline_notes"])
    assert doc["sentinel"]["signals"] == 2
    assert doc["sentinel"]["target_hits"] == 1
    assert doc["sentinel"]["hit_rate_pct"] == 50.0
    assert doc["sentinel"]["avg_mfe"] == 22.5
    assert doc["sentinel"]["test_cvd"] is True

    page = render_session_report(doc)
    assert "2026-10-29" in page
    assert "₹22,690.65" in page
    assert "₹22,944.95" in page
    assert "50.0%" in page
    assert "<script" not in page


def test_write_is_one_file_per_date(tmp_path: Path):
    _init(tmp_path)
    write_session_report("2026-10-29", tmp_path)
    write_session_report("2026-10-29", tmp_path)
    folder = tmp_path / "data" / "journal" / "reports"
    assert sorted(p.name for p in folder.iterdir()) == [
        "2026-10-29.html",
        "2026-10-29.json",
    ]
