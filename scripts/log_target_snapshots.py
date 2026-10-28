"""Target Engine Snapshot Logger (Priority Data Capture).

Captures full-chain snapshots at regular intervals (default 60s) during NSE market hours
into a dedicated SQLite WAL database (``data/journal/target_engine.db``) for Target Engine
walk-forward replay, model calibration, and backtesting.

Usage:
  python scripts/log_target_snapshots.py                      # Loop every 60s during market hours
  python scripts/log_target_snapshots.py --once                # Capture single snapshot and exit
  python scripts/log_target_snapshots.py --loop 30             # Loop every 30 seconds
  python scripts/log_target_snapshots.py --once --ignore-session  # Off-hours testing
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from app.data_access import load_dashboard
from app.obs import book_fields, event
from target_engine.time_utils import calculate_fractional_t, to_ist

log = logging.getLogger("target_engine.logger")

DB_PATH = ROOT / "data" / "journal" / "target_engine.db"
_OPEN_IST = (9, 15)
_CLOSE_IST = (15, 30)


def _init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Initialize SQLite database with WAL mode and schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                spot REAL NOT NULL,
                fut REAL NOT NULL,
                atm_iv REAL,
                skew_25d REAL,
                pcr_oi REAL,
                max_pain REAL,
                expiry TEXT,
                dte REAL,
                n_contracts INTEGER,
                source TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(symbol, ts)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS chain_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                strike REAL NOT NULL,
                option_type TEXT NOT NULL,
                ltp REAL,
                iv REAL,
                oi REAL,
                oi_change REAL,
                volume REAL,
                FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chain_snapshot ON chain_rows(snapshot_id)")

    return conn


def _in_session(ist: datetime) -> bool:
    """Check if current time is within NSE trading session."""
    import config
    return config.is_market_open(ist, include_fno_extended=False)


def record_snapshot(symbol: str = "NIFTY") -> bool:
    """Fetch current dashboard bundle and persist full chain snapshot."""
    ist_now = to_ist()
    log.info(f"Fetching market chain for {symbol} at {ist_now.strftime('%Y-%m-%d %H:%M:%S IST')}...")

    try:
        dash = load_dashboard(window="0dte_to_next_monthly", symbol=symbol, live_source="dhan")
    except Exception as exc:
        log.error(f"Failed to fetch market data: {exc}")
        event("target_snapshot.failed", level="ERROR", ok=False, workflow="target_snapshot", symbol=symbol, error=str(exc))
        return False

    if dash is None or dash.levels is None:
        log.warning(f"No valid levels returned for {symbol}")
        event("target_snapshot.failed", level="WARNING", ok=False, workflow="target_snapshot", symbol=symbol, error="no levels")
        return False

    spot = float(dash.levels.spot or 0.0)
    fut = float(getattr(dash.levels, "future_price", None) or spot)
    atm_iv = float(getattr(dash.levels, "atm_iv", None) or 0.0)
    skew = float(getattr(dash.levels, "skew_25d", None) or 0.0)
    pcr = float(dash.oi_volume.get("pcr_oi", 0.0)) if dash.oi_volume else 0.0
    max_pain = float(dash.levels.max_pain or 0.0)
    primary_expiry = str(dash.expiries[0]) if dash.expiries else ""
    dte = calculate_fractional_t(ist_now, primary_expiry) * 365.0 if primary_expiry else 0.0

    conn = _init_db()
    try:
        with conn:
            cur = conn.execute("""
                INSERT INTO snapshots (
                    ts, symbol, spot, fut, atm_iv, skew_25d, pcr_oi, max_pain,
                    expiry, dte, n_contracts, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ist_now.isoformat(),
                symbol,
                spot,
                fut,
                atm_iv,
                skew,
                pcr,
                max_pain,
                primary_expiry,
                dte,
                dash.n_contracts,
                dash.source,
            ))
            snap_id = cur.lastrowid

            # Insert per-contract rows if available
            contracts = dash.contracts
            if contracts is not None and hasattr(contracts, "iterrows"):
                rows = []
                for _, r in contracts.iterrows():
                    strike = float(r.get("strike", 0.0))
                    opt_type = str(r.get("type", "")).upper()
                    ltp = float(r.get("ltp", 0.0)) if r.get("ltp") is not None else None
                    iv = float(r.get("iv", 0.0)) if r.get("iv") is not None else None
                    oi = float(r.get("oi", 0.0)) if r.get("oi") is not None else None
                    oi_chg = float(r.get("change_oi", 0.0)) if r.get("change_oi") is not None else None
                    vol = float(r.get("volume", 0.0)) if r.get("volume") is not None else None
                    rows.append((snap_id, strike, opt_type, ltp, iv, oi, oi_chg, vol))

                if rows:
                    conn.executemany("""
                        INSERT INTO chain_rows (
                            snapshot_id, strike, option_type, ltp, iv, oi, oi_change, volume
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, rows)

        log.info(f"Captured snapshot #{snap_id} for {symbol}: spot={spot:.1f}, ATM IV={atm_iv:.2%}, PCR={pcr:.2f}")
        fields = book_fields(dash)
        fields["symbol"] = symbol
        fields["snapshot_id"] = snap_id
        event("target_snapshot.stored", workflow="target_snapshot", **fields)
        return True
    except Exception as exc:
        log.error(f"Error persisting snapshot to SQLite: {exc}")
        event("target_snapshot.failed", level="ERROR", ok=False, workflow="target_snapshot", symbol=symbol, error=str(exc))
        return False
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Target Engine Market Snapshot Logger")
    parser.add_argument("--symbol", default="NIFTY", help="Symbol to log (default: NIFTY)")
    parser.add_argument("--once", action="store_true", help="Record one snapshot then exit")
    parser.add_argument("--loop", type=int, default=60, help="Loop interval in seconds (default: 60)")
    parser.add_argument("--ignore-session", action="store_true", help="Capture even outside market hours")
    args = parser.parse_args()
    from app.obs import boot
    boot("log_target_snapshots", loop=not args.once)

    log.info(f"Starting Target Engine logger for {args.symbol} (interval: {args.loop}s)...")

    if args.once:
        ist = to_ist()
        if not args.ignore_session and not _in_session(ist):
            log.info("Outside market hours, exiting (--ignore-session not set)")
            return
        record_snapshot(args.symbol)
        return

    while True:
        ist = to_ist()
        if args.ignore_session or _in_session(ist):
            record_snapshot(args.symbol)
        else:
            log.info(f"Market closed ({ist.strftime('%H:%M:%S IST')}). Waiting for next session...")
        _time.sleep(max(5, args.loop))


if __name__ == "__main__":
    main()
