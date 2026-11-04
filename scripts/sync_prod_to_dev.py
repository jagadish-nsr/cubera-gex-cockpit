#!/usr/bin/env python3
"""Sync production data for a specific date or date range to the dev app (overwriting dev data).

Synchronizes:
  1. Date-partitioned journal files:
     - 1m futures: data/journal/dhan_futures/<SYMBOL>/<DATE>.parquet
     - 1m options: data/journal/dhan_intraday/<STORE>/<DATE>.parquet
     - EOD options: data/journal/options_eod/<SYMBOL>/<DATE>.csv
     - Participant OI: data/journal/participant_oi/<DATE>.csv
     - OI strikes & gamma blast: data/journal/oi_strikes*/, gamma_blast*/
     - Sentinel replay parquets: data/journal/sentinel/<SYMBOL>/<DATE>.parquet
     - EOD snapshots, reports, and cache files matching <DATE>
  2. Aggregated CSV journals (merges/overwrites rows for the target date(s)):
     - data/journal/sessions.csv
     - data/journal/timeseries/<SYMBOL>.csv
  3. SQLite database records (deletes dev rows for the target date(s) and copies prod rows):
     - data/sentinel.db (sentinel_bars, sentinel_signals, signal_outcomes, sentinel_daily_cvd)
     - data/journal/market_snapshot.db (market_auctions_history, session_snapshots)
     - data/journal/recommendations.db (recommendation_records, score_history, events, ml_features)
     - data/journal/big_money.db (events, futures_state)
     - data/journal/commentary.db (commentary_entries, bites, events, session_recaps)
     - data/journal/paper_trades.db (paper_trades)
     - data/journal/target_engine.db (snapshots, target_runs, scenarios, candidate_trades)
     - data/journal/admin_ops.db (admin_alerts)

Usage:
  # Single date
  python scripts/sync_prod_to_dev.py 2026-11-03
  python scripts/sync_prod_to_dev.py --date 2026-11-03

  # Date range
  python scripts/sync_prod_to_dev.py 2026-11-01 2026-11-03
  python scripts/sync_prod_to_dev.py --start 2026-11-01 --end 2026-11-03

  # Dry run (inspect what will be synced without modifying disk)
  python scripts/sync_prod_to_dev.py 2026-11-03 --dry-run

  # Explicit prod directory
  python scripts/sync_prod_to_dev.py 2026-11-03 --prod-dir /path/to/cubera-gex-cockpit-prod
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _print(msg: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(msg, flush=True)


def parse_date(val: str | date) -> date:
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date format: {val}. Expected YYYY-MM-DD") from exc


def date_range(start: date, end: date) -> list[str]:
    if end < start:
        raise ValueError(f"end_date ({end}) is before start_date ({start})")
    span = (end - start).days + 1
    if span > 365:
        raise ValueError(f"Date span {span} days exceeds maximum allowed 365 days")
    days = []
    curr = start
    while curr <= end:
        days.append(curr.isoformat())
        curr += timedelta(days=1)
    return days


def resolve_prod_dir(custom_path: str | Path | None = None, dev_dir: Path | None = None) -> Path:
    dev = Path(dev_dir or ROOT).resolve()
    candidates: list[Path] = []
    if custom_path:
        candidates.append(Path(custom_path).resolve())
    env_prod = os.environ.get("PROD_DIR")
    if env_prod:
        candidates.append(Path(env_prod).resolve())
    candidates.append((dev.parent / "cubera-gex-cockpit-prod").resolve())
    candidates.append(Path.home() / "MyProjects" / "Applications" / "cubera-gex-cockpit-prod")

    for cand in candidates:
        if cand.is_dir() and (cand / "data").is_dir():
            return cand

    for cand in candidates:
        if cand.is_dir():
            return cand

    checked = "\n  - ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        f"Production repository directory could not be found. Checked:\n  - {checked}\n"
        "Specify --prod-dir <PATH> or set environment variable PROD_DIR."
    )


# ==============================================================================
# 1. Date-Partitioned File Synchronization
# ==============================================================================

def sync_partitioned_files(
    prod_dir: Path,
    dev_dir: Path,
    target_dates: set[str],
    dry_run: bool = False,
    log: Callable[[str], None] = _print,
) -> list[dict]:
    """Scan prod_dir for date-partitioned files and copy them to dev_dir, overwriting."""
    synced = []
    search_roots = [
        prod_dir / "data" / "journal",
        prod_dir / "data" / ".cache",
        prod_dir / "docs",
    ]

    for search_root in search_roots:
        if not search_root.is_dir():
            continue

        for root_path, _dirs, files in os.walk(search_root):
            rel_dir = Path(root_path).relative_to(prod_dir)
            for file_name in files:
                matched_date = None
                for d in target_dates:
                    if d in file_name:
                        matched_date = d
                        break
                if not matched_date:
                    continue

                prod_file = Path(root_path) / file_name
                dev_file = dev_dir / rel_dir / file_name

                synced.append({
                    "date": matched_date,
                    "rel_path": str(rel_dir / file_name),
                    "size_bytes": prod_file.stat().st_size if prod_file.exists() else 0,
                })

                if not dry_run:
                    dev_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(prod_file, dev_file)

    # Latest snapshot pickle files if target_dates include today
    today_iso = date.today().isoformat()
    if today_iso in target_dates:
        prod_journal = prod_dir / "data" / "journal"
        if prod_journal.is_dir():
            for pkl in prod_journal.glob("latest_snap_*.pkl"):
                dev_pkl = dev_dir / "data" / "journal" / pkl.name
                synced.append({
                    "date": today_iso,
                    "rel_path": str(Path("data") / "journal" / pkl.name),
                    "size_bytes": pkl.stat().st_size,
                })
                if not dry_run:
                    dev_pkl.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(pkl, dev_pkl)

    return synced


# ==============================================================================
# 2. Combined CSV Synchronization (sessions.csv, timeseries/*.csv)
# ==============================================================================

def sync_combined_csv(
    prod_file: Path,
    dev_file: Path,
    target_dates: set[str],
    date_col: str = "date_ist",
    ts_col: str = "timestamp_utc",
    dry_run: bool = False,
) -> dict:
    """Overwrites rows for target_dates in dev_file using rows from prod_file."""
    if not prod_file.is_file():
        return {"file": str(dev_file), "replaced_rows": 0, "inserted_rows": 0}

    def _matches(row: dict) -> bool:
        d = row.get(date_col) or ""
        ts = row.get(ts_col) or ""
        return (d in target_dates) or (ts[:10] in target_dates)

    # Read prod rows for target dates
    prod_rows: list[dict] = []
    prod_fields: list[str] = []
    with open(prod_file, mode="r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        prod_fields = list(reader.fieldnames or [])
        for r in reader:
            if _matches(r):
                prod_rows.append(r)

    # Read dev rows, retaining non-matching rows
    dev_rows: list[dict] = []
    dev_fields: list[str] = []
    replaced_count = 0
    if dev_file.is_file():
        with open(dev_file, mode="r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            dev_fields = list(reader.fieldnames or [])
            for r in reader:
                if _matches(r):
                    replaced_count += 1
                else:
                    dev_rows.append(r)

    fields = prod_fields or dev_fields
    combined = dev_rows + prod_rows

    # Sort combined rows chronologically if timestamp exists
    if ts_col in fields:
        combined.sort(key=lambda x: x.get(ts_col, ""))
    elif date_col in fields:
        combined.sort(key=lambda x: x.get(date_col, ""))

    if not dry_run and fields:
        dev_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = dev_file.with_suffix(".tmp")
        with open(tmp_file, mode="w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(combined)
        tmp_file.replace(dev_file)

    return {
        "file": str(dev_file),
        "replaced_rows": replaced_count,
        "inserted_rows": len(prod_rows),
    }


def sync_all_combined_csvs(
    prod_dir: Path,
    dev_dir: Path,
    target_dates: set[str],
    dry_run: bool = False,
) -> list[dict]:
    results = []
    # 1. sessions.csv
    p_sess = prod_dir / "data" / "journal" / "sessions.csv"
    d_sess = dev_dir / "data" / "journal" / "sessions.csv"
    if p_sess.is_file():
        results.append(sync_combined_csv(p_sess, d_sess, target_dates, dry_run=dry_run))

    # 2. timeseries/*.csv
    p_ts_dir = prod_dir / "data" / "journal" / "timeseries"
    d_ts_dir = dev_dir / "data" / "journal" / "timeseries"
    if p_ts_dir.is_dir():
        for p_csv in p_ts_dir.glob("*.csv"):
            d_csv = d_ts_dir / p_csv.name
            results.append(sync_combined_csv(p_csv, d_csv, target_dates, dry_run=dry_run))

    return results


# ==============================================================================
# 3. SQLite Database Synchronization
# ==============================================================================

def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)


def _connect_rw(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return [r[1] for r in cur.fetchall()]
    except sqlite3.Error:
        return []


def _ensure_table(prod_conn: sqlite3.Connection, dev_conn: sqlite3.Connection, table: str) -> bool:
    dev_cols = _get_columns(dev_conn, table)
    if dev_cols:
        return True
    row = prod_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row and row[0]:
        dev_conn.execute(row[0])
        dev_conn.commit()
        return True
    return False


def _sync_table(
    prod_conn: sqlite3.Connection,
    dev_conn: sqlite3.Connection,
    table: str,
    select_sql: str,
    delete_sql: str,
    params: Sequence,
    dry_run: bool = False,
) -> dict:
    if not _ensure_table(prod_conn, dev_conn, table):
        return {"table": table, "deleted": 0, "inserted": 0}

    dev_cols = set(_get_columns(dev_conn, table))
    prod_cols = _get_columns(prod_conn, table)
    common_cols = [c for c in prod_cols if c in dev_cols]
    if not common_cols:
        return {"table": table, "deleted": 0, "inserted": 0}

    # Fetch prod rows
    prod_cur = prod_conn.execute(select_sql, params)
    rows = prod_cur.fetchall()
    select_col_names = [d[0] for d in prod_cur.description]
    col_indices = [select_col_names.index(c) for c in common_cols if c in select_col_names]
    matched_cols = [select_col_names[i] for i in col_indices]

    if dry_run:
        return {"table": table, "deleted": 0, "inserted": len(rows)}

    # Delete existing dev rows
    del_cur = dev_conn.execute(delete_sql, params)
    deleted_count = del_cur.rowcount

    # Insert prod rows
    inserted_count = 0
    if rows and matched_cols:
        placeholders = ", ".join(["?"] * len(matched_cols))
        col_list = ", ".join(matched_cols)
        insert_sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
        filtered_rows = [tuple(r[i] for i in col_indices) for r in rows]
        dev_conn.executemany(insert_sql, filtered_rows)
        inserted_count = len(rows)

    dev_conn.commit()
    return {"table": table, "deleted": deleted_count, "inserted": inserted_count}


def sync_sentinel_db(
    prod_db: Path,
    dev_db: Path,
    target_dates: list[str],
    dry_run: bool = False,
) -> list[dict]:
    if not prod_db.is_file():
        return []

    p_conn = _connect_ro(prod_db)
    d_conn = _connect_rw(dev_db)
    results = []
    try:
        q_marks = ", ".join(["?"] * len(target_dates))

        # 1. sentinel_bars
        res = _sync_table(
            p_conn, d_conn, "sentinel_bars",
            select_sql=f"SELECT * FROM sentinel_bars WHERE substr(start_ts, 1, 10) IN ({q_marks})",
            delete_sql=f"DELETE FROM sentinel_bars WHERE substr(start_ts, 1, 10) IN ({q_marks})",
            params=target_dates,
            dry_run=dry_run,
        )
        results.append(res)

        # 2. sentinel_signals
        res = _sync_table(
            p_conn, d_conn, "sentinel_signals",
            select_sql=f"SELECT * FROM sentinel_signals WHERE substr(bar_start_ts, 1, 10) IN ({q_marks})",
            delete_sql=f"DELETE FROM sentinel_signals WHERE substr(bar_start_ts, 1, 10) IN ({q_marks})",
            params=target_dates,
            dry_run=dry_run,
        )
        results.append(res)

        # 3. signal_outcomes
        res = _sync_table(
            p_conn, d_conn, "signal_outcomes",
            select_sql=f"""
                SELECT * FROM signal_outcomes
                 WHERE substr(evaluated_at, 1, 10) IN ({q_marks})
                    OR event_id IN (
                        SELECT event_id FROM sentinel_signals
                         WHERE substr(bar_start_ts, 1, 10) IN ({q_marks})
                    )
            """,
            delete_sql=f"""
                DELETE FROM signal_outcomes
                 WHERE substr(evaluated_at, 1, 10) IN ({q_marks})
                    OR event_id IN (
                        SELECT event_id FROM sentinel_signals
                         WHERE substr(bar_start_ts, 1, 10) IN ({q_marks})
                    )
            """,
            params=target_dates + target_dates,
            dry_run=dry_run,
        )
        results.append(res)

        # 4. sentinel_daily_cvd
        res = _sync_table(
            p_conn, d_conn, "sentinel_daily_cvd",
            select_sql=f"SELECT * FROM sentinel_daily_cvd WHERE trade_date IN ({q_marks})",
            delete_sql=f"DELETE FROM sentinel_daily_cvd WHERE trade_date IN ({q_marks})",
            params=target_dates,
            dry_run=dry_run,
        )
        results.append(res)

        if not dry_run:
            try:
                d_conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                pass
    finally:
        p_conn.close()
        d_conn.close()

    return results


def sync_market_snapshot_db(
    prod_db: Path,
    dev_db: Path,
    target_dates: list[str],
    dry_run: bool = False,
) -> list[dict]:
    if not prod_db.is_file():
        return []

    p_conn = _connect_ro(prod_db)
    d_conn = _connect_rw(dev_db)
    results = []
    try:
        q_marks = ", ".join(["?"] * len(target_dates))

        # 1. market_auctions_history
        res = _sync_table(
            p_conn, d_conn, "market_auctions_history",
            select_sql=f"SELECT * FROM market_auctions_history WHERE trade_date IN ({q_marks})",
            delete_sql=f"DELETE FROM market_auctions_history WHERE trade_date IN ({q_marks})",
            params=target_dates,
            dry_run=dry_run,
        )
        results.append(res)

        # 2. session_snapshots (sync current snapshot rows if today or latest completed session is included)
        today_iso = date.today().isoformat()
        if today_iso in target_dates or target_dates[-1] >= today_iso:
            res = _sync_table(
                p_conn, d_conn, "session_snapshots",
                select_sql="SELECT * FROM session_snapshots",
                delete_sql="DELETE FROM session_snapshots",
                params=(),
                dry_run=dry_run,
            )
            results.append(res)
    finally:
        p_conn.close()
        d_conn.close()

    return results


def sync_recommendations_db(
    prod_db: Path,
    dev_db: Path,
    target_dates: list[str],
    dry_run: bool = False,
) -> list[dict]:
    if not prod_db.is_file():
        return []

    p_conn = _connect_ro(prod_db)
    d_conn = _connect_rw(dev_db)
    results = []
    try:
        q_marks = ", ".join(["?"] * len(target_dates))

        # 1. recommendation_records
        res = _sync_table(
            p_conn, d_conn, "recommendation_records",
            select_sql=f"""
                SELECT * FROM recommendation_records
                 WHERE substr(triggered_at, 1, 10) IN ({q_marks})
                    OR substr(created_at, 1, 10) IN ({q_marks})
            """,
            delete_sql=f"""
                DELETE FROM recommendation_records
                 WHERE substr(triggered_at, 1, 10) IN ({q_marks})
                    OR substr(created_at, 1, 10) IN ({q_marks})
            """,
            params=target_dates + target_dates,
            dry_run=dry_run,
        )
        results.append(res)

        # 2. recommendation_score_history
        res = _sync_table(
            p_conn, d_conn, "recommendation_score_history",
            select_sql=f"SELECT * FROM recommendation_score_history WHERE substr(recorded_at, 1, 10) IN ({q_marks})",
            delete_sql=f"DELETE FROM recommendation_score_history WHERE substr(recorded_at, 1, 10) IN ({q_marks})",
            params=target_dates,
            dry_run=dry_run,
        )
        results.append(res)

        # 3. recommendation_events
        res = _sync_table(
            p_conn, d_conn, "recommendation_events",
            select_sql=f"SELECT * FROM recommendation_events WHERE substr(event_ts, 1, 10) IN ({q_marks})",
            delete_sql=f"DELETE FROM recommendation_events WHERE substr(event_ts, 1, 10) IN ({q_marks})",
            params=target_dates,
            dry_run=dry_run,
        )
        results.append(res)

        # 4. ml_features_dataset
        res = _sync_table(
            p_conn, d_conn, "ml_features_dataset",
            select_sql=f"SELECT * FROM ml_features_dataset WHERE substr(triggered_at, 1, 10) IN ({q_marks})",
            delete_sql=f"DELETE FROM ml_features_dataset WHERE substr(triggered_at, 1, 10) IN ({q_marks})",
            params=target_dates,
            dry_run=dry_run,
        )
        results.append(res)
    finally:
        p_conn.close()
        d_conn.close()

    return results


def sync_big_money_db(
    prod_db: Path,
    dev_db: Path,
    target_dates: list[str],
    dry_run: bool = False,
) -> list[dict]:
    if not prod_db.is_file():
        return []

    p_conn = _connect_ro(prod_db)
    d_conn = _connect_rw(dev_db)
    results = []
    try:
        q_marks = ", ".join(["?"] * len(target_dates))

        res = _sync_table(
            p_conn, d_conn, "events",
            select_sql=f"SELECT * FROM events WHERE day IN ({q_marks}) OR substr(ts_ist, 1, 10) IN ({q_marks})",
            delete_sql=f"DELETE FROM events WHERE day IN ({q_marks}) OR substr(ts_ist, 1, 10) IN ({q_marks})",
            params=target_dates + target_dates,
            dry_run=dry_run,
        )
        results.append(res)

        today_iso = date.today().isoformat()
        if today_iso in target_dates:
            res_fut = _sync_table(
                p_conn, d_conn, "futures_state",
                select_sql="SELECT * FROM futures_state",
                delete_sql="DELETE FROM futures_state",
                params=(),
                dry_run=dry_run,
            )
            results.append(res_fut)
    finally:
        p_conn.close()
        d_conn.close()

    return results


def sync_commentary_db(
    prod_db: Path,
    dev_db: Path,
    target_dates: list[str],
    dry_run: bool = False,
) -> list[dict]:
    if not prod_db.is_file():
        return []

    p_conn = _connect_ro(prod_db)
    d_conn = _connect_rw(dev_db)
    results = []
    try:
        q_marks = ", ".join(["?"] * len(target_dates))

        for table, col in [
            ("commentary_entries", "ts"),
            ("commentary_bites", "ts"),
            ("commentary_events", "ts"),
            ("commentary_suppressed", "ts"),
        ]:
            res = _sync_table(
                p_conn, d_conn, table,
                select_sql=f"SELECT * FROM {table} WHERE substr({col}, 1, 10) IN ({q_marks})",
                delete_sql=f"DELETE FROM {table} WHERE substr({col}, 1, 10) IN ({q_marks})",
                params=target_dates,
                dry_run=dry_run,
            )
            results.append(res)

        res_recap = _sync_table(
            p_conn, d_conn, "session_recaps",
            select_sql=f"SELECT * FROM session_recaps WHERE session_date IN ({q_marks})",
            delete_sql=f"DELETE FROM session_recaps WHERE session_date IN ({q_marks})",
            params=target_dates,
            dry_run=dry_run,
        )
        results.append(res_recap)
    finally:
        p_conn.close()
        d_conn.close()

    return results


def sync_paper_trades_db(
    prod_db: Path,
    dev_db: Path,
    target_dates: list[str],
    dry_run: bool = False,
) -> list[dict]:
    if not prod_db.is_file():
        return []

    p_conn = _connect_ro(prod_db)
    d_conn = _connect_rw(dev_db)
    results = []
    try:
        q_marks = ", ".join(["?"] * len(target_dates))
        res = _sync_table(
            p_conn, d_conn, "paper_trades",
            select_sql=f"SELECT * FROM paper_trades WHERE substr(entry_ts, 1, 10) IN ({q_marks})",
            delete_sql=f"DELETE FROM paper_trades WHERE substr(entry_ts, 1, 10) IN ({q_marks})",
            params=target_dates,
            dry_run=dry_run,
        )
        results.append(res)
    finally:
        p_conn.close()
        d_conn.close()

    return results


def sync_target_engine_db(
    prod_db: Path,
    dev_db: Path,
    target_dates: list[str],
    dry_run: bool = False,
) -> list[dict]:
    if not prod_db.is_file():
        return []

    p_conn = _connect_ro(prod_db)
    d_conn = _connect_rw(dev_db)
    results = []
    try:
        q_marks = ", ".join(["?"] * len(target_dates))
        for table in ["snapshots", "target_runs"]:
            res = _sync_table(
                p_conn, d_conn, table,
                select_sql=f"SELECT * FROM {table} WHERE substr(timestamp, 1, 10) IN ({q_marks})",
                delete_sql=f"DELETE FROM {table} WHERE substr(timestamp, 1, 10) IN ({q_marks})",
                params=target_dates,
                dry_run=dry_run,
            )
            results.append(res)
    finally:
        p_conn.close()
        d_conn.close()

    return results


def sync_admin_ops_db(
    prod_db: Path,
    dev_db: Path,
    target_dates: list[str],
    dry_run: bool = False,
) -> list[dict]:
    if not prod_db.is_file():
        return []

    p_conn = _connect_ro(prod_db)
    d_conn = _connect_rw(dev_db)
    results = []
    try:
        q_marks = ", ".join(["?"] * len(target_dates))
        res = _sync_table(
            p_conn, d_conn, "admin_alerts",
            select_sql=f"""
                SELECT * FROM admin_alerts
                 WHERE session_date IN ({q_marks})
                    OR substr(created_at, 1, 10) IN ({q_marks})
            """,
            delete_sql=f"""
                DELETE FROM admin_alerts
                 WHERE session_date IN ({q_marks})
                    OR substr(created_at, 1, 10) IN ({q_marks})
            """,
            params=target_dates + target_dates,
            dry_run=dry_run,
        )
        results.append(res)
    finally:
        p_conn.close()
        d_conn.close()

    return results


# ==============================================================================
# Main Orchestrator Function
# ==============================================================================

def sync_prod_to_dev(
    start_date: str | date,
    end_date: str | date | None = None,
    prod_dir: str | Path | None = None,
    dev_dir: str | Path | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = _print,
) -> dict:
    """Synchronize all production data for start_date..end_date to the dev environment."""
    s = parse_date(start_date)
    e = parse_date(end_date or start_date)
    dates = date_range(s, e)
    date_set = set(dates)

    dev_root = Path(dev_dir or ROOT).resolve()
    prod_root = resolve_prod_dir(prod_dir, dev_dir=dev_root)

    mode_str = " [DRY-RUN - NO CHANGES WILL BE WRITTEN]" if dry_run else ""
    log(f"=== Syncing Production Data to Dev{mode_str} ===")
    log(f"  Source (Prod): {prod_root}")
    log(f"  Target (Dev):  {dev_root}")
    log(f"  Date Span:     {dates[0]} .. {dates[-1]} ({len(dates)} day{'s' if len(dates) > 1 else ''})\n")

    summary: dict = {
        "status": "ok",
        "dry_run": dry_run,
        "prod_dir": str(prod_root),
        "dev_dir": str(dev_root),
        "start_date": dates[0],
        "end_date": dates[-1],
        "days_count": len(dates),
        "files_synced": [],
        "csv_rows_synced": [],
        "db_records_synced": [],
    }

    # 1. Sync Date-Partitioned Files
    log("▶ 1. Synchronizing date-partitioned files...")
    files = sync_partitioned_files(prod_root, dev_root, date_set, dry_run=dry_run, log=log)
    summary["files_synced"] = files
    log(f"  ✓ Found and {'would copy' if dry_run else 'overwrote'} {len(files)} files.")
    for f in files[:10]:
        log(f"    • {f['rel_path']} ({f['size_bytes']:,} bytes)")
    if len(files) > 10:
        log(f"    ... and {len(files) - 10} more files.")

    # 2. Sync Combined CSVs
    log("\n▶ 2. Synchronizing combined journal CSVs...")
    csv_res = sync_all_combined_csvs(prod_root, dev_root, date_set, dry_run=dry_run)
    summary["csv_rows_synced"] = csv_res
    for c in csv_res:
        log(f"  ✓ {Path(c['file']).name}: replaced {c['replaced_rows']} existing dev rows, "
            f"inserted {c['inserted_rows']} prod rows.")

    # 3. Sync SQLite Databases
    log("\n▶ 3. Synchronizing SQLite database ledgers...")
    db_syncs = [
        ("sentinel.db", sync_sentinel_db(prod_root / "data" / "sentinel.db", dev_root / "data" / "sentinel.db", dates, dry_run=dry_run)),
        ("market_snapshot.db", sync_market_snapshot_db(prod_root / "data" / "journal" / "market_snapshot.db", dev_root / "data" / "journal" / "market_snapshot.db", dates, dry_run=dry_run)),
        ("recommendations.db", sync_recommendations_db(prod_root / "data" / "journal" / "recommendations.db", dev_root / "data" / "journal" / "recommendations.db", dates, dry_run=dry_run)),
        ("big_money.db", sync_big_money_db(prod_root / "data" / "journal" / "big_money.db", dev_root / "data" / "journal" / "big_money.db", dates, dry_run=dry_run)),
        ("commentary.db", sync_commentary_db(prod_root / "data" / "journal" / "commentary.db", dev_root / "data" / "journal" / "commentary.db", dates, dry_run=dry_run)),
        ("paper_trades.db", sync_paper_trades_db(prod_root / "data" / "journal" / "paper_trades.db", dev_root / "data" / "journal" / "paper_trades.db", dates, dry_run=dry_run)),
        ("target_engine.db", sync_target_engine_db(prod_root / "data" / "journal" / "target_engine.db", dev_root / "data" / "journal" / "target_engine.db", dates, dry_run=dry_run)),
        ("admin_ops.db", sync_admin_ops_db(prod_root / "data" / "journal" / "admin_ops.db", dev_root / "data" / "journal" / "admin_ops.db", dates, dry_run=dry_run)),
    ]

    total_inserted = 0
    for db_name, tbl_results in db_syncs:
        for r in tbl_results:
            summary["db_records_synced"].append({"db": db_name, **r})
            total_inserted += r["inserted"]
            if r["inserted"] > 0 or r["deleted"] > 0:
                log(f"  ✓ {db_name} [{r['table']}]: replaced {r['deleted']} rows, inserted {r['inserted']} prod rows.")

    action_label = "Planned" if dry_run else "Successfully synced and overwritten"
    log(f"\n✅ {action_label}: {len(files)} files, {len(csv_res)} CSV ledgers, and {total_inserted} SQLite records for {len(dates)} day(s).")
    return summary


# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync production data for a date or date range into the dev app (overwrites).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("dates", nargs="*", help="Target date (YYYY-MM-DD) or range (START_DATE END_DATE)")
    parser.add_argument("--date", help="Single date to sync (YYYY-MM-DD)")
    parser.add_argument("--start", help="Start date of range (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date of range (YYYY-MM-DD)")
    parser.add_argument("--prod-dir", help="Explicit path to cubera-gex-cockpit-prod")
    parser.add_argument("--dev-dir", help="Explicit path to dev workspace (defaults to current repo)")
    parser.add_argument("--dry-run", action="store_true", help="Inspect what will be synced without writing")
    args = parser.parse_args()

    # Determine start and end date
    start = args.start or args.date
    end = args.end or args.date

    if args.dates:
        if len(args.dates) == 1:
            start = start or args.dates[0]
            end = end or args.dates[0]
        elif len(args.dates) == 2:
            start = start or args.dates[0]
            end = end or args.dates[1]
        else:
            parser.error("Specify at most two positional dates: START_DATE [END_DATE]")

    if not start:
        # Default to today
        today = date.today().isoformat()
        _print(f"ℹ️ No date specified, defaulting to today: {today}")
        start = today
        end = today

    try:
        sync_prod_to_dev(
            start_date=start,
            end_date=end,
            prod_dir=args.prod_dir,
            dev_dir=args.dev_dir,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        _print(f"❌ ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

