"""sentinel/storage/db.py — OrderFlow Sentinel SQLite Database Ledger.

Implements Sentinel §10:
  - SQLite WAL mode for non-blocking concurrent reads & writes
  - Tables:
      * sentinel_bars: 1m & 5m historical candle flow
      * sentinel_signals: Real-time signals with scoring and context tags
      * signal_outcomes: Forward-return evaluations (+1m, +3m, +5m, +15m MFE/MAE)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sentinel.models import Bar, SentinelSignal, SignalOutcome

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = ROOT / "data" / "sentinel.db"
_WRITE_LOCK = threading.Lock()


def _get_write_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sentinel_bars (
            instrument_id    TEXT NOT NULL,
            timeframe        TEXT NOT NULL,
            start_ts         TEXT NOT NULL,
            open             REAL NOT NULL,
            high             REAL NOT NULL,
            low              REAL NOT NULL,
            close            REAL NOT NULL,
            volume_units     INTEGER NOT NULL,
            delta            REAL NOT NULL,
            delta_ratio      REAL NOT NULL,
            cvd              REAL NOT NULL,
            vwap             REAL NOT NULL,
            vwap_sigma_1     REAL NOT NULL,
            vwap_sigma_2     REAL NOT NULL,
            vwap_lower_1     REAL NOT NULL,
            vwap_lower_2     REAL NOT NULL,
            oi               INTEGER NOT NULL,
            delta_oi         INTEGER NOT NULL,
            PRIMARY KEY (instrument_id, timeframe, start_ts)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sentinel_signals (
            event_id            TEXT PRIMARY KEY,
            instrument_id       TEXT NOT NULL,
            timeframe           TEXT NOT NULL,
            bar_start_ts        TEXT NOT NULL,
            type                TEXT NOT NULL,
            category            TEXT NOT NULL,
            bias                TEXT NOT NULL,
            score               INTEGER NOT NULL,
            confidence          TEXT NOT NULL,
            rvol_tod            REAL NOT NULL,
            delta               REAL NOT NULL,
            delta_ratio         REAL NOT NULL,
            oi_change           INTEGER NOT NULL,
            context_tags_json   TEXT NOT NULL,
            insight_text        TEXT NOT NULL,
            invalidation_level  REAL NOT NULL,
            rule_set_version    TEXT NOT NULL,
            status              TEXT NOT NULL,
            gex_alignment       TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            event_id            TEXT PRIMARY KEY,
            instrument_id       TEXT NOT NULL,
            signal_type         TEXT NOT NULL,
            entry_price         REAL NOT NULL,
            invalidation_level  REAL NOT NULL,
            ret_1m_pts          REAL,
            ret_3m_pts          REAL,
            ret_5m_pts          REAL,
            ret_15m_pts         REAL,
            ret_30m_pts         REAL,
            mfe_pts             REAL NOT NULL DEFAULT 0.0,
            mae_pts             REAL NOT NULL DEFAULT 0.0,
            stopped_out         INTEGER NOT NULL DEFAULT 0,
            target_hit          INTEGER NOT NULL DEFAULT 0,
            evaluated_at        TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_bars_ts ON sentinel_bars (instrument_id, timeframe, start_ts DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_ts ON sentinel_signals (instrument_id, bar_start_ts DESC)
    """)
    conn.commit()
    return conn


def _get_read_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _DB_PATH.exists():
        ensure_sentinel_schema()
    uri = f"file:{_DB_PATH}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        ensure_sentinel_schema()
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn



def ensure_sentinel_schema() -> None:
    with _WRITE_LOCK:
        conn = _get_write_conn()
        conn.close()


def save_bar(bar: Bar) -> None:
    """Save or update a completed Bar."""
    with _WRITE_LOCK:
        conn = _get_write_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO sentinel_bars (
                    instrument_id, timeframe, start_ts, open, high, low, close,
                    volume_units, delta, delta_ratio, cvd, vwap,
                    vwap_sigma_1, vwap_sigma_2, vwap_lower_1, vwap_lower_2,
                    oi, delta_oi
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                bar.instrument_id, bar.timeframe, bar.start_ts.isoformat(),
                bar.open, bar.high, bar.low, bar.close,
                bar.volume_units, bar.delta, bar.delta_ratio, bar.cvd,
                bar.vwap, bar.vwap_sigma_1, bar.vwap_sigma_2, bar.vwap_lower_1, bar.vwap_lower_2,
                bar.oi, bar.delta_oi,
            ))
            conn.commit()
        finally:
            conn.close()


def save_signal(sig: SentinelSignal) -> None:
    """Save an emitted SentinelSignal."""
    with _WRITE_LOCK:
        conn = _get_write_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO sentinel_signals (
                    event_id, instrument_id, timeframe, bar_start_ts, type,
                    category, bias, score, confidence, rvol_tod,
                    delta, delta_ratio, oi_change, context_tags_json,
                    insight_text, invalidation_level, rule_set_version,
                    status, gex_alignment
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                sig.event_id, sig.instrument_id, sig.timeframe, sig.bar_start_ts.isoformat(),
                sig.type, sig.category, sig.bias, sig.score, sig.confidence, sig.rvol_tod,
                sig.delta, sig.delta_ratio, sig.oi_change, json.dumps(sig.context_tags),
                sig.insight_text, sig.invalidation_level, sig.rule_set_version,
                sig.status, sig.gex_alignment,
            ))
            conn.commit()
        finally:
            conn.close()


def list_recent_bars(instrument_id: str, timeframe: str = "1m", limit: int = 120) -> list[Bar]:
    """Retrieve recent bars in chronological order (oldest first)."""
    conn = _get_read_conn()
    try:
        rows = conn.execute("""
            SELECT * FROM sentinel_bars
            WHERE instrument_id = ? AND timeframe = ?
            ORDER BY start_ts DESC LIMIT ?
        """, (instrument_id, timeframe, limit)).fetchall()
        bars = []
        for r in reversed(rows):
            bars.append(Bar(
                instrument_id=r["instrument_id"],
                timeframe=r["timeframe"],
                start_ts=datetime.fromisoformat(r["start_ts"]),
                open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                volume_units=r["volume_units"], volume_lots=round(r["volume_units"] / 65.0, 1),
                buy_vol_est=round((r["volume_units"] + r["delta"]) / 2.0, 1),
                sell_vol_est=round((r["volume_units"] - r["delta"]) / 2.0, 1),
                delta=r["delta"], delta_ratio=r["delta_ratio"], delta_quality=1.0,
                cvd=r["cvd"], vwap=r["vwap"],
                vwap_sigma_1=r["vwap_sigma_1"], vwap_sigma_2=r["vwap_sigma_2"],
                vwap_lower_1=r["vwap_lower_1"], vwap_lower_2=r["vwap_lower_2"],
                oi=r["oi"], delta_oi=r["delta_oi"],
            ))

        # If higher timeframe bars (e.g. 3m, 5m, 15m) were requested but none stored yet, dynamically synthesize from 1m bars
        if not bars and timeframe.endswith("m") and timeframe != "1m":
            try:
                target_min = int(timeframe.rstrip("m"))
                from sentinel.engine.aggregator import aggregate_bars
                bars_1m = list_recent_bars(instrument_id, timeframe="1m", limit=limit * target_min)
                if bars_1m:
                    bars_agg = aggregate_bars(bars_1m, target_minutes=target_min)
                    try:
                        for b in bars_agg:
                            save_bar(b)
                    except Exception:
                        pass
                    return bars_agg[-limit:]
            except Exception:
                pass

        return bars
    finally:
        conn.close()


def list_recent_signals(instrument_id: str | None = None, limit: int = 50) -> list[SentinelSignal]:
    """Retrieve recent signals, newest first."""
    conn = _get_read_conn()
    try:
        if instrument_id:
            rows = conn.execute("""
                SELECT * FROM sentinel_signals
                WHERE instrument_id = ?
                ORDER BY bar_start_ts DESC LIMIT ?
            """, (instrument_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM sentinel_signals
                ORDER BY bar_start_ts DESC LIMIT ?
            """, (limit,)).fetchall()

        signals = []
        for r in rows:
            signals.append(SentinelSignal(
                event_id=r["event_id"],
                instrument_id=r["instrument_id"],
                timeframe=r["timeframe"],
                bar_start_ts=datetime.fromisoformat(r["bar_start_ts"]),
                type=r["type"],
                category=r["category"],
                bias=r["bias"],
                score=r["score"],
                confidence=r["confidence"],
                rvol_tod=r["rvol_tod"],
                delta=r["delta"],
                delta_ratio=r["delta_ratio"],
                oi_change=r["oi_change"],
                context_tags=json.loads(r["context_tags_json"]),
                insight_text=r["insight_text"],
                invalidation_level=r["invalidation_level"],
                rule_set_version=r["rule_set_version"],
                status=r["status"],
                gex_alignment=r["gex_alignment"],
            ))
        return signals
    finally:
        conn.close()


def save_outcome(outcome: SignalOutcome) -> None:
    """Save or update a SignalOutcome evaluation."""
    with _WRITE_LOCK:
        conn = _get_write_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO signal_outcomes (
                    event_id, instrument_id, signal_type, entry_price, invalidation_level,
                    ret_1m_pts, ret_3m_pts, ret_5m_pts, ret_15m_pts, ret_30m_pts,
                    mfe_pts, mae_pts, stopped_out, target_hit, evaluated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                outcome.event_id, outcome.instrument_id, outcome.signal_type,
                outcome.entry_price, outcome.invalidation_level,
                outcome.ret_1m_pts, outcome.ret_3m_pts, outcome.ret_5m_pts,
                outcome.ret_15m_pts, outcome.ret_30m_pts,
                outcome.mfe_pts, outcome.mae_pts,
                1 if outcome.stopped_out else 0,
                1 if outcome.target_hit else 0,
                outcome.evaluated_at.isoformat() if outcome.evaluated_at else None,
            ))
            conn.commit()
        finally:
            conn.close()


def list_recent_outcomes(instrument_id: str | None = None, limit: int = 50) -> list[SignalOutcome]:
    """Retrieve recent signal outcomes, newest first."""
    conn = _get_read_conn()
    try:
        if instrument_id:
            rows = conn.execute("""
                SELECT * FROM signal_outcomes
                WHERE instrument_id = ?
                ORDER BY evaluated_at DESC LIMIT ?
            """, (instrument_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM signal_outcomes
                ORDER BY evaluated_at DESC LIMIT ?
            """, (limit,)).fetchall()

        outcomes = []
        for r in rows:
            eval_at = datetime.fromisoformat(r["evaluated_at"]) if r["evaluated_at"] else None
            outcomes.append(SignalOutcome(
                event_id=r["event_id"],
                instrument_id=r["instrument_id"],
                signal_type=r["signal_type"],
                entry_price=r["entry_price"],
                invalidation_level=r["invalidation_level"],
                ret_1m_pts=r["ret_1m_pts"],
                ret_3m_pts=r["ret_3m_pts"],
                ret_5m_pts=r["ret_5m_pts"],
                ret_15m_pts=r["ret_15m_pts"],
                ret_30m_pts=r["ret_30m_pts"],
                mfe_pts=r["mfe_pts"],
                mae_pts=r["mae_pts"],
                stopped_out=bool(r["stopped_out"]),
                target_hit=bool(r["target_hit"]),
                evaluated_at=eval_at,
            ))
        return outcomes
    finally:
        conn.close()


def clear_sentinel_data(instrument_id: str | None = None) -> None:
    """Clear bars, signals, and outcomes for an instrument or all if None."""
    with _WRITE_LOCK:
        conn = _get_write_conn()
        try:
            if instrument_id:
                conn.execute("DELETE FROM sentinel_bars WHERE instrument_id = ?", (instrument_id,))
                conn.execute("DELETE FROM sentinel_signals WHERE instrument_id = ?", (instrument_id,))
                conn.execute("DELETE FROM signal_outcomes WHERE instrument_id = ?", (instrument_id,))
            else:
                conn.execute("DELETE FROM sentinel_bars")
                conn.execute("DELETE FROM sentinel_signals")
                conn.execute("DELETE FROM signal_outcomes")
            conn.commit()
        finally:
            conn.close()


def purge_old_sentinel_data(
    bars_1m_days: int = 60,
    bars_5m_days: int = 180,
    signals_days: int = 365,
) -> dict[str, int]:
    """Purge historical sentinel data older than retention limits.

    Returns dict with count of deleted rows per category:
      {'bars_1m': int, 'bars_5m': int, 'signals': int, 'outcomes': int}
    """
    with _WRITE_LOCK:
        conn = _get_write_conn()
        try:
            c1 = conn.execute(
                "DELETE FROM sentinel_bars WHERE timeframe = '1m' AND start_ts < date('now', ?)",
                (f"-{bars_1m_days} days",),
            ).rowcount
            c2 = conn.execute(
                "DELETE FROM sentinel_bars WHERE timeframe = '5m' AND start_ts < date('now', ?)",
                (f"-{bars_5m_days} days",),
            ).rowcount
            c3 = conn.execute(
                "DELETE FROM sentinel_signals WHERE bar_start_ts < date('now', ?)",
                (f"-{signals_days} days",),
            ).rowcount
            c4 = conn.execute(
                "DELETE FROM signal_outcomes WHERE event_id NOT IN (SELECT event_id FROM sentinel_signals)"
            ).rowcount
            conn.commit()
            return {
                "bars_1m": c1,
                "bars_5m": c2,
                "signals": c3,
                "outcomes": c4,
            }
        finally:
            conn.close()


