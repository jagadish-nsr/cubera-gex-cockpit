"""commentary/storage/db.py — SQLite WAL Persistence and In-Memory Cache for Commentary."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from commentary.models import CommentaryEntry, SuggestionBite, Event
try:
    from commentary.models import to_ist
except Exception:
    def to_ist(ts: Any) -> datetime:
        if ts is None:
            return datetime.now(IST)
        if isinstance(ts, str):
            try:
                cleaned = ts.replace("Z", "+00:00")
                dt = datetime.fromisoformat(cleaned)
            except Exception:
                return datetime.now(IST)
        else:
            dt = ts
        if dt.tzinfo is not None:
            return dt.astimezone(IST)
        if dt.hour < 7:
            return dt.replace(tzinfo=timezone.utc).astimezone(IST)
        return dt.replace(tzinfo=IST)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = ROOT / "data" / "journal" / "commentary.db"
_WRITE_LOCK = threading.Lock()
IST = timezone(timedelta(hours=5, minutes=30))

_RECENT_ENTRIES_CACHE: list[dict[str, Any]] = []
_CACHE_LOCK = threading.Lock()
_CACHE_INITIALIZED = False
_CACHE_SYNC_TS = 0.0


def clear_commentary_cache() -> None:
    """Force flush in-memory commentary cache so next read syncs from SQLite."""
    global _CACHE_INITIALIZED, _CACHE_SYNC_TS
    with _CACHE_LOCK:
        _RECENT_ENTRIES_CACHE.clear()
        _CACHE_INITIALIZED = False
        _CACHE_SYNC_TS = 0.0


def _now_ist_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def _get_write_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn


def _get_read_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_commentary_schema() -> None:
    """Initialize commentary database tables with indexes."""
    with _WRITE_LOCK, _get_write_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS commentary_entries (
            entry_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            phase TEXT NOT NULL,
            headline TEXT NOT NULL,
            body TEXT NOT NULL,
            event_ids_json TEXT NOT NULL,
            verified INTEGER DEFAULT 1,
            bites_json TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            facts_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_commentary_sym_ts ON commentary_entries(symbol, ts);

        CREATE TABLE IF NOT EXISTS commentary_bites (
            bite_id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            bite_type TEXT NOT NULL,
            headline TEXT NOT NULL,
            action_frame TEXT NOT NULL,
            levels_json TEXT NOT NULL,
            risk_json TEXT NOT NULL,
            condition_json TEXT NOT NULL,
            badge_json TEXT NOT NULL,
            state TEXT NOT NULL,
            resolved_ts TEXT,
            outcome_json TEXT,
            pinned_rec_id TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_bites_sym_state ON commentary_bites(symbol, state);
        CREATE INDEX IF NOT EXISTS idx_bites_pinned ON commentary_bites(pinned_rec_id);

        CREATE TABLE IF NOT EXISTS commentary_events (
            event_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            facts_json TEXT NOT NULL,
            magnitude_sigma REAL NOT NULL,
            quality_flags_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS commentary_suppressed (
            suppress_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS session_recaps (
            session_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            recap_text TEXT NOT NULL,
            open_threads_json TEXT NOT NULL,
            stats_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            PRIMARY KEY(session_date, symbol)
        );
        """)
        try:
            conn.execute("ALTER TABLE commentary_entries ADD COLUMN facts_json TEXT DEFAULT '{}'")
        except Exception:
            pass


def save_commentary_entry(entry: CommentaryEntry, symbol: str = "NIFTY") -> None:
    """Persist commentary entry and attached suggestion bites in a single WAL transaction."""
    ensure_commentary_schema()
    from commentary.models import to_ist
    entry_ts_str = to_ist(entry.ts).isoformat()
    bites_data = [
        {
            "id": b.id,
            "entry_id": entry.id,
            "ts": to_ist(b.ts).isoformat(),
            "type": b.type,
            "headline": b.headline,
            "action_frame": b.action_frame,
            "levels": b.levels,
            "risk": b.risk,
            "condition": b.condition,
            "badge": b.badge,
            "state": b.state,
            "pinned_rec_id": b.pinned_rec_id,
        }
        for b in entry.bites
    ]
    facts_dict = getattr(entry, "facts", {}) or {}

    with _WRITE_LOCK, _get_write_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO commentary_entries
            (entry_id, ts, symbol, phase, headline, body, event_ids_json, verified, bites_json, tags_json, facts_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry_ts_str,
                symbol.upper(),
                entry.phase,
                entry.headline,
                entry.body,
                json.dumps(entry.event_ids),
                1 if entry.verified else 0,
                json.dumps(bites_data),
                json.dumps(entry.tags),
                json.dumps(facts_dict),
            ),
        )

        for b in entry.bites:
            conn.execute(
                """
                INSERT OR REPLACE INTO commentary_bites
                (bite_id, entry_id, ts, symbol, bite_type, headline, action_frame,
                 levels_json, risk_json, condition_json, badge_json, state, resolved_ts,
                 outcome_json, pinned_rec_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    b.id,
                    entry.id,
                    to_ist(b.ts).isoformat(),
                    symbol.upper(),
                    b.type,
                    b.headline,
                    b.action_frame,
                    json.dumps(b.levels),
                    json.dumps(b.risk),
                    json.dumps(b.condition),
                    json.dumps(b.badge),
                    b.state,
                    to_ist(b.resolved_ts).isoformat() if b.resolved_ts else None,
                    json.dumps(b.outcome) if b.outcome else None,
                    b.pinned_rec_id,
                ),
            )

    # Update in-memory cache
    cached_entry = {
        "entry_id": entry.id,
        "ts": entry_ts_str,
        "symbol": symbol.upper(),
        "phase": entry.phase,
        "headline": entry.headline,
        "body": entry.body,
        "event_ids": entry.event_ids,
        "verified": entry.verified,
        "bites": bites_data,
        "tags": entry.tags,
        "facts": facts_dict,
    }
    with _CACHE_LOCK:
        _RECENT_ENTRIES_CACHE.insert(0, cached_entry)
        if len(_RECENT_ENTRIES_CACHE) > 100:
            _RECENT_ENTRIES_CACHE.pop()


def get_recent_commentary_entries(symbol: str = "NIFTY", limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve recent commentary entries with low-latency cache backed by SQLite WAL."""
    global _CACHE_INITIALIZED, _CACHE_SYNC_TS
    ensure_commentary_schema()
    from commentary.models import to_ist

    now_t = time.time()
    with _CACHE_LOCK:
        if _CACHE_INITIALIZED and (now_t - _CACHE_SYNC_TS < 3.0) and _RECENT_ENTRIES_CACHE:
            filtered = [e for e in _RECENT_ENTRIES_CACHE if e.get("symbol", "").upper() == symbol.upper()]
            return filtered[:limit]

    # Load from DB to populate cache
    try:
        with _get_read_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM commentary_entries
                WHERE symbol = ?
                ORDER BY ts DESC LIMIT ?
                """,
                (symbol.upper(), limit),
            ).fetchall()

            entries: list[dict[str, Any]] = []
            for r in rows:
                try:
                    bites_list = json.loads(r["bites_json"])
                    for b in bites_list:
                        if "ts" in b:
                            b["ts"] = to_ist(b["ts"]).isoformat()
                        if b.get("valid_until"):
                            b["valid_until"] = to_ist(b["valid_until"]).isoformat()
                        if b.get("resolved_ts"):
                            b["resolved_ts"] = to_ist(b["resolved_ts"]).isoformat()
                except Exception:
                    bites_list = []
                try:
                    event_ids = json.loads(r["event_ids_json"])
                except Exception:
                    event_ids = []
                try:
                    tags = json.loads(r["tags_json"])
                except Exception:
                    tags = []
                try:
                    facts_dict = json.loads(r["facts_json"]) if "facts_json" in r.keys() and r["facts_json"] else {}
                except Exception:
                    facts_dict = {}

                entries.append({
                    "entry_id": r["entry_id"],
                    "ts": to_ist(r["ts"]).isoformat(),
                    "symbol": r["symbol"],
                    "phase": r["phase"],
                    "headline": r["headline"],
                    "body": r["body"],
                    "event_ids": event_ids,
                    "verified": bool(r["verified"]),
                    "bites": bites_list,
                    "tags": tags,
                    "facts": facts_dict,
                })

            with _CACHE_LOCK:
                _RECENT_ENTRIES_CACHE.clear()
                _RECENT_ENTRIES_CACHE.extend(entries)
                _CACHE_INITIALIZED = True
                _CACHE_SYNC_TS = time.time()

            return entries
    except Exception as exc:
        log.error("Failed to load commentary entries: %s", exc)
        return []


def get_pinned_commentary_cache() -> dict[str, str]:
    """Return in-memory pinned recommendation commentary mapped by rec_id."""
    from commentary.detectors.pinned import _PINNED_COMMENTARY_CACHE
    return dict(_PINNED_COMMENTARY_CACHE)
