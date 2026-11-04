import csv
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.sync_prod_to_dev import (
    date_range,
    parse_date,
    resolve_prod_dir,
    sync_combined_csv,
    sync_market_snapshot_db,
    sync_partitioned_files,
    sync_prod_to_dev,
    sync_recommendations_db,
    sync_sentinel_db,
)


class TestSyncProdToDev(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name).resolve()

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_date_parsing_and_range(self):
        self.assertEqual(parse_date("2026-11-03"), date(2026, 11, 3))
        self.assertEqual(parse_date(date(2026, 11, 3)), date(2026, 11, 3))
        with self.assertRaises(ValueError):
            parse_date("invalid-date")

        days = date_range(date(2026, 11, 1), date(2026, 11, 3))
        self.assertEqual(days, ["2026-11-01", "2026-11-02", "2026-11-03"])

        with self.assertRaises(ValueError):
            date_range(date(2026, 11, 3), date(2026, 11, 1))

    def test_resolve_prod_dir(self):
        mock_prod = self.tmp_path / "cubera-gex-cockpit-prod"
        (mock_prod / "data").mkdir(parents=True)

        # Explicit path
        found = resolve_prod_dir(mock_prod)
        self.assertEqual(found, mock_prod)

        # Sibling directory
        mock_dev = self.tmp_path / "cubera-gex-cockpit"
        mock_dev.mkdir()
        found_sibling = resolve_prod_dir(dev_dir=mock_dev)
        self.assertEqual(found_sibling, mock_prod)

    def test_sync_partitioned_files(self):
        prod = self.tmp_path / "prod"
        dev = self.tmp_path / "dev"

        p_dhan = prod / "data" / "journal" / "dhan_futures" / "NIFTY"
        p_dhan.mkdir(parents=True)
        (p_dhan / "2026-11-03.parquet").write_text("prod-nifty-data")
        (p_dhan / "2026-11-02.parquet").write_text("old-nifty-data")

        p_eod = prod / "data" / "journal" / "options_eod" / "NIFTY"
        p_eod.mkdir(parents=True)
        (p_eod / "2026-11-03.csv").write_text("strike,oi\n24000,100")

        p_doc = prod / "docs"
        p_doc.mkdir(parents=True)
        (p_doc / "session-evaluation-2026-11-03.md").write_text("# Report 2026-11-03")

        # Pre-existing dev file that should be overwritten
        d_dhan = dev / "data" / "journal" / "dhan_futures" / "NIFTY"
        d_dhan.mkdir(parents=True)
        (d_dhan / "2026-11-03.parquet").write_text("dev-stale-data")

        # 1. Dry run
        res_dry = sync_partitioned_files(prod, dev, {"2026-11-03"}, dry_run=True)
        self.assertEqual(len(res_dry), 3)
        self.assertEqual((d_dhan / "2026-11-03.parquet").read_text(), "dev-stale-data")

        # 2. Real sync
        res_real = sync_partitioned_files(prod, dev, {"2026-11-03"}, dry_run=False)
        self.assertEqual(len(res_real), 3)
        self.assertEqual((d_dhan / "2026-11-03.parquet").read_text(), "prod-nifty-data")
        self.assertEqual((dev / "data" / "journal" / "options_eod" / "NIFTY" / "2026-11-03.csv").read_text(), "strike,oi\n24000,100")
        self.assertEqual((dev / "docs" / "session-evaluation-2026-11-03.md").read_text(), "# Report 2026-11-03")
        # Ensure 2026-11-02 was NOT copied
        self.assertFalse((dev / "data" / "journal" / "dhan_futures" / "NIFTY" / "2026-11-02.parquet").exists())

    def test_sync_combined_csv(self):
        p_csv = self.tmp_path / "prod_sessions.csv"
        d_csv = self.tmp_path / "dev_sessions.csv"

        with open(p_csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["date_ist", "timestamp_utc", "spot"])
            w.writeheader()
            w.writerow({"date_ist": "2026-11-02", "timestamp_utc": "2026-11-02T10:00:00Z", "spot": "24100"})
            w.writerow({"date_ist": "2026-11-03", "timestamp_utc": "2026-11-03T10:00:00Z", "spot": "24200"})

        with open(d_csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["date_ist", "timestamp_utc", "spot"])
            w.writeheader()
            w.writerow({"date_ist": "2026-11-01", "timestamp_utc": "2026-11-01T10:00:00Z", "spot": "24000"})
            w.writerow({"date_ist": "2026-11-03", "timestamp_utc": "2026-11-03T09:30:00Z", "spot": "99999"})  # Dev stale row

        res = sync_combined_csv(p_csv, d_csv, {"2026-11-03"}, dry_run=False)
        self.assertEqual(res["replaced_rows"], 1)
        self.assertEqual(res["inserted_rows"], 1)

        with open(d_csv, "r", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["date_ist"], "2026-11-01")
        self.assertEqual(rows[0]["spot"], "24000")
        self.assertEqual(rows[1]["date_ist"], "2026-11-03")
        self.assertEqual(rows[1]["spot"], "24200")  # Overwritten with prod!

    def test_sync_sentinel_db(self):
        p_db = self.tmp_path / "prod_sentinel.db"
        d_db = self.tmp_path / "dev_sentinel.db"

        schema = """
            CREATE TABLE sentinel_bars (
                instrument_id TEXT, timeframe TEXT, start_ts TEXT, open REAL, high REAL, low REAL, close REAL, volume_units INT,
                delta REAL, delta_ratio REAL, cvd REAL, vwap REAL, vwap_sigma_1 REAL, vwap_sigma_2 REAL, vwap_lower_1 REAL,
                vwap_lower_2 REAL, oi INT, delta_oi INT,
                PRIMARY KEY (instrument_id, timeframe, start_ts)
            );
            CREATE TABLE sentinel_signals (
                event_id TEXT PRIMARY KEY, instrument_id TEXT, timeframe TEXT, bar_start_ts TEXT, type TEXT, category TEXT,
                bias TEXT, score INT, confidence TEXT, rvol_tod REAL, delta REAL, delta_ratio REAL, oi_change INT,
                context_tags_json TEXT, insight_text TEXT, invalidation_level REAL, rule_set_version TEXT, status TEXT, gex_alignment TEXT
            );
            CREATE TABLE signal_outcomes (
                event_id TEXT PRIMARY KEY, instrument_id TEXT, signal_type TEXT, entry_price REAL, invalidation_level REAL,
                mfe_pts REAL, mae_pts REAL, stopped_out INT, target_hit INT, evaluated_at TEXT
            );
            CREATE TABLE sentinel_daily_cvd (
                trade_date TEXT, instrument_id TEXT, symbol TEXT, closing_cvd REAL, buy_volume INT, sell_volume INT,
                total_volume INT, net_delta REAL, closing_price REAL, vwap REAL, updated_at TEXT,
                PRIMARY KEY (trade_date, instrument_id)
            );
        """
        for db_path in (p_db, d_db):
            with sqlite3.connect(db_path) as conn:
                conn.executescript(schema)

        # Populate prod
        with sqlite3.connect(p_db) as conn:
            conn.execute(
                "INSERT INTO sentinel_bars (instrument_id, timeframe, start_ts, open, high, low, close, volume_units, delta, delta_ratio, cvd, vwap, vwap_sigma_1, vwap_sigma_2, vwap_lower_1, vwap_lower_2, oi, delta_oi) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("NIFTY-FUT", "1m", "2026-11-03 09:15:00", 24200, 24210, 24190, 24205, 100, 20, 0.2, 20, 24200, 10, 20, 10, 20, 500, 50)
            )
            conn.execute(
                "INSERT INTO sentinel_signals (event_id, instrument_id, timeframe, bar_start_ts, type, category, bias, score, confidence, rvol_tod, delta, delta_ratio, oi_change, context_tags_json, insight_text, invalidation_level, rule_set_version, status, gex_alignment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("SIG_PROD_1", "NIFTY-FUT", "1m", "2026-11-03 09:15:00", "ABSORPTION", "WALL", "BULLISH", 85, "HIGH", 1.5, 20, 0.2, 50, "[]", "Test", 24190, "v1", "ACTIVE", "ALIGNED")
            )
            conn.execute(
                "INSERT INTO sentinel_daily_cvd VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("2026-11-03", "NIFTY-FUT", "NIFTY", 1500.0, 5000, 3500, 8500, 1500.0, 24250.0, 24210.0, "2026-11-03T15:30:00")
            )

        # Populate stale dev
        with sqlite3.connect(d_db) as conn:
            conn.execute(
                "INSERT INTO sentinel_bars (instrument_id, timeframe, start_ts, open, high, low, close, volume_units, delta, delta_ratio, cvd, vwap, vwap_sigma_1, vwap_sigma_2, vwap_lower_1, vwap_lower_2, oi, delta_oi) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("NIFTY-FUT", "1m", "2026-11-03 09:15:00", 99999, 99999, 99999, 99999, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
            )

        res = sync_sentinel_db(p_db, d_db, ["2026-11-03"], dry_run=False)
        self.assertEqual(len(res), 4)

        # Verify dev has been overwritten with prod
        with sqlite3.connect(d_db) as conn:
            bar = conn.execute("SELECT open, close FROM sentinel_bars WHERE start_ts LIKE '2026-11-03%'").fetchone()
            self.assertEqual(bar, (24200.0, 24205.0))

            sig = conn.execute("SELECT event_id FROM sentinel_signals").fetchone()
            self.assertEqual(sig, ("SIG_PROD_1",))

            cvd = conn.execute("SELECT closing_cvd FROM sentinel_daily_cvd WHERE trade_date='2026-11-03'").fetchone()
            self.assertEqual(cvd, (1500.0,))

    def test_sync_recommendations_and_snapshots(self):
        p_rec = self.tmp_path / "prod_rec.db"
        d_rec = self.tmp_path / "dev_rec.db"

        schema_rec = """
            CREATE TABLE recommendation_records (
                rec_id TEXT PRIMARY KEY, symbol TEXT, triggered_at TEXT, created_at TEXT, initial_spot REAL
            );
            CREATE TABLE recommendation_score_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT, rec_id TEXT, recorded_at TEXT, score INT
            );
        """
        for db in (p_rec, d_rec):
            with sqlite3.connect(db) as conn:
                conn.executescript(schema_rec)

        with sqlite3.connect(p_rec) as conn:
            conn.execute("INSERT INTO recommendation_records VALUES (?,?,?,?,?)",
                         ("REC_1", "NIFTY", "2026-11-03T10:00:00", "2026-11-03 10:00:00", 24200.0))
            conn.execute("INSERT INTO recommendation_score_history (rec_id, recorded_at, score) VALUES (?,?,?)",
                         ("REC_1", "2026-11-03T10:05:00", 90))

        with sqlite3.connect(d_rec) as conn:
            conn.execute("INSERT INTO recommendation_records VALUES (?,?,?,?,?)",
                         ("REC_1", "NIFTY", "2026-11-03T10:00:00", "2026-11-03 10:00:00", 11111.0))

        sync_recommendations_db(p_rec, d_rec, ["2026-11-03"], dry_run=False)

        with sqlite3.connect(d_rec) as conn:
            row = conn.execute("SELECT initial_spot FROM recommendation_records WHERE rec_id='REC_1'").fetchone()
            self.assertEqual(row, (24200.0,))

    def test_full_sync_orchestration(self):
        prod = self.tmp_path / "prod"
        dev = self.tmp_path / "dev"

        (prod / "data" / "journal" / "dhan_futures" / "NIFTY").mkdir(parents=True)
        (prod / "data" / "journal" / "dhan_futures" / "NIFTY" / "2026-11-02.parquet").write_text("fut-2")
        (prod / "data" / "journal" / "dhan_futures" / "NIFTY" / "2026-11-03.parquet").write_text("fut-3")

        summary = sync_prod_to_dev(
            start_date="2026-11-02",
            end_date="2026-11-03",
            prod_dir=prod,
            dev_dir=dev,
            dry_run=False,
            log=lambda _: None,
        )
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["days_count"], 2)
        self.assertEqual(len(summary["files_synced"]), 2)
        self.assertEqual((dev / "data" / "journal" / "dhan_futures" / "NIFTY" / "2026-11-02.parquet").read_text(), "fut-2")
        self.assertEqual((dev / "data" / "journal" / "dhan_futures" / "NIFTY" / "2026-11-03.parquet").read_text(), "fut-3")

    def test_cli_help(self):
        proc = subprocess.run(
            [sys.executable, "scripts/sync_prod_to_dev.py", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Sync production data for a date or date range into the dev app", proc.stdout)


if __name__ == "__main__":
    unittest.main()
