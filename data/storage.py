"""Local CSV-based historical data store for the Indian GEX engine.

Each index gets its own subdirectory under data/journal/eod/:
  data/journal/eod/<SYMBOL>/YYYY-MM-DD_futures.csv   - EOD futures OI/price
  data/journal/eod/<SYMBOL>/YYYY-MM-DD_oi_strikes.csv - EOD per-strike OI

Purging:
  - call purge_old_data(months=2) to delete records older than 60 days.
  - Runs at most once per day (guarded by data/journal/.last_purge).

Data retention philosophy:
  - Keep everything for the current + last 2 months.
  - Purge aggressively to avoid unbounded disk growth.
  - All writes are append-safe (idempotent for the same date).
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
EOD_DIR       = ROOT / "data" / "journal" / "eod"
PURGE_GUARD   = ROOT / "data" / "journal" / ".last_purge"

# ---------- Schema -----------------------------------------------------------
FUTURES_FIELDS = [
    "date_ist", "symbol", "expiry",
    "open", "high", "low", "close", "prev_close", "dprice",
    "oi", "doi", "volume",
]

OI_STRIKE_EOD_FIELDS = [
    "date_ist", "symbol", "expiry", "strike", "type",
    "oi", "doi", "volume", "close_price",
]

# ---------- Path helpers -----------------------------------------------------

def _eod_dir(symbol: str) -> Path:
    return EOD_DIR / symbol.upper()


def _futures_path(symbol: str, d: date) -> Path:
    return _eod_dir(symbol) / f"{d.isoformat()}_futures.csv"


def _oi_strikes_eod_path(symbol: str, d: date) -> Path:
    return _eod_dir(symbol) / f"{d.isoformat()}_oi_strikes.csv"


# ---------- Writers ----------------------------------------------------------

def save_futures_eod(symbol: str, d: date, rows: list[dict]) -> Path:
    """Persist EOD futures rows for a given date. Idempotent (overwrites)."""
    path = _futures_path(symbol, d)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FUTURES_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    log.debug("Saved %d futures rows for %s on %s", len(rows), symbol, d)
    return path


def save_oi_strikes_eod(symbol: str, d: date, rows: list[dict]) -> Path:
    """Persist EOD per-strike OI rows for a given date. Idempotent (overwrites)."""
    path = _oi_strikes_eod_path(symbol, d)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OI_STRIKE_EOD_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    log.debug("Saved %d OI-strike EOD rows for %s on %s", len(rows), symbol, d)
    return path


# ---------- Readers ----------------------------------------------------------

def load_futures_eod(symbol: str, d: date) -> list[dict] | None:
    """Load EOD futures rows for a given date. Returns None if not on disk."""
    import pandas as pd
    path = _futures_path(symbol, d)
    if not path.exists():
        return None
    df = pd.read_csv(path)
    return df.to_dict(orient="records") if not df.empty else []


def load_oi_strikes_eod(symbol: str, d: date) -> list[dict] | None:
    """Load EOD per-strike OI for a given date. Returns None if not on disk."""
    import pandas as pd
    path = _oi_strikes_eod_path(symbol, d)
    if not path.exists():
        return None
    df = pd.read_csv(path)
    return df.to_dict(orient="records") if not df.empty else []


def available_eod_dates(symbol: str) -> list[date]:
    """Sorted list of dates for which futures EOD data exists."""
    d = _eod_dir(symbol)
    if not d.exists():
        return []
    dates = []
    for p in d.glob("*_futures.csv"):
        try:
            dates.append(date.fromisoformat(p.stem.replace("_futures", "")))
        except ValueError:
            pass
    return sorted(dates)


def last_n_eod_futures(symbol: str, n: int, before: date | None = None) -> list[dict]:
    """Load the last `n` trading days of EOD futures data, optionally before `before`."""
    all_dates = available_eod_dates(symbol)
    if before:
        all_dates = [d for d in all_dates if d < before]
    recent = all_dates[-n:]
    rows = []
    for d in recent:
        day_rows = load_futures_eod(symbol, d) or []
        rows.extend(day_rows)
    return rows


# ---------- Purging ----------------------------------------------------------

def purge_old_data(months: int = 2, force: bool = False) -> int:
    """Delete any journal/eod files older than `months` months.

    Runs at most once per calendar day unless `force=True`. Returns the number
    of files deleted. Also purges holiday cache files older than 2 years.
    """
    today = date.today()
    cutoff = date(today.year, today.month, 1) - timedelta(days=months * 30)

    # Guard: only run once per day
    if not force and PURGE_GUARD.exists():
        try:
            last_purge = date.fromisoformat(PURGE_GUARD.read_text().strip())
            if last_purge >= today:
                return 0
        except (ValueError, OSError):
            pass

    deleted = 0
    # Purge EOD files
    if EOD_DIR.exists():
        for p in EOD_DIR.rglob("*.csv"):
            # filename pattern: YYYY-MM-DD_futures.csv or YYYY-MM-DD_oi_strikes.csv
            stem = p.stem.split("_")[0]
            try:
                file_date = date.fromisoformat(stem)
                if file_date < cutoff:
                    p.unlink()
                    deleted += 1
            except ValueError:
                pass

    # Purge old intraday futures / timeseries / oi_strikes journals
    for subdir_name in ("timeseries", "oi_strikes", "futures", "futures_intraday", "gamma_blast"):
        subdir = ROOT / "data" / "journal" / subdir_name
        if not subdir.exists():
            continue
        for p in subdir.rglob("*.csv"):
            stem = p.stem
            try:
                file_date = date.fromisoformat(stem)
                if file_date < cutoff:
                    p.unlink()
                    deleted += 1
            except ValueError:
                pass

    # Purge old holiday cache (keep only last 3 years)
    from data.holidays import HOLIDAY_DIR
    if HOLIDAY_DIR.exists():
        for p in HOLIDAY_DIR.glob("*.json"):
            try:
                year = int(p.stem)
                if year < today.year - 2:
                    p.unlink()
                    deleted += 1
            except ValueError:
                pass

    # Update guard
    try:
        PURGE_GUARD.parent.mkdir(parents=True, exist_ok=True)
        PURGE_GUARD.write_text(str(today))
    except OSError:
        pass

    if deleted:
        log.info("Purged %d old journal files (cutoff: %s)", deleted, cutoff)
    return deleted
