"""data/journal/recommendations_store.py — Persistence, Bookmarks, Score Evolution & ML Dataset.

Design principles:
  - SQLite WAL mode for non-blocking concurrent reads from Streamlit.
  - Single-writer thread lock for reliable background writes.
  - Point-in-time feature capture for future AI/ML training dataset.
  - Fast in-memory caching for zero-latency UI rendering (<1ms).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = ROOT / "data" / "journal" / "recommendations.db"
_WRITE_LOCK = threading.Lock()
IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_str() -> str:
    """Return formatted ISO IST datetime string."""
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


def ensure_schema() -> None:
    """Initialize SQLite tables for recommendations, score trajectories, events, and ML telemetry."""
    with _WRITE_LOCK, _get_write_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS recommendation_records (
            rec_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            strike REAL NOT NULL,
            option_symbol TEXT NOT NULL,
            play_type TEXT NOT NULL,
            play_badge TEXT NOT NULL,
            triggered_at TEXT NOT NULL,
            initial_spot REAL NOT NULL,
            initial_ltp REAL NOT NULL,
            initial_score INTEGER NOT NULL,
            current_score INTEGER NOT NULL,
            conviction_tier TEXT NOT NULL,
            target_1 REAL NOT NULL,
            target_1_spot REAL NOT NULL,
            target_2 REAL NOT NULL,
            target_2_spot REAL NOT NULL,
            stop_loss REAL NOT NULL,
            spot_invalidation REAL NOT NULL,
            spot_invalidation_text TEXT NOT NULL,
            risk_reward_ratio TEXT NOT NULL,
            entry_trigger TEXT NOT NULL,
            rationale TEXT NOT NULL,
            catalyst_tags_json TEXT NOT NULL,
            is_bookmarked INTEGER DEFAULT 0,
            bookmarked_at TEXT,
            status TEXT DEFAULT 'ACTIVE',
            outcome_ts TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_rec_symbol_status ON recommendation_records(symbol, status);
        CREATE INDEX IF NOT EXISTS idx_rec_bookmarked ON recommendation_records(is_bookmarked);

        CREATE TABLE IF NOT EXISTS recommendation_score_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rec_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            score INTEGER NOT NULL,
            score_delta INTEGER NOT NULL,
            current_spot REAL NOT NULL,
            current_ltp REAL NOT NULL,
            reason TEXT NOT NULL,
            factors_json TEXT NOT NULL,
            FOREIGN KEY(rec_id) REFERENCES recommendation_records(rec_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_score_hist_rec ON recommendation_score_history(rec_id, recorded_at);

        CREATE TABLE IF NOT EXISTS recommendation_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rec_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_ts TEXT NOT NULL,
            spot_at_event REAL NOT NULL,
            ltp_at_event REAL NOT NULL,
            pnl_pct REAL NOT NULL,
            message TEXT NOT NULL,
            was_bookmarked INTEGER NOT NULL,
            alert_acknowledged INTEGER DEFAULT 0,
            FOREIGN KEY(rec_id) REFERENCES recommendation_records(rec_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_events_rec ON recommendation_events(rec_id);
        CREATE INDEX IF NOT EXISTS idx_events_ack ON recommendation_events(alert_acknowledged, was_bookmarked);

        CREATE TABLE IF NOT EXISTS ml_features_dataset (
            sample_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            triggered_at TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            side TEXT NOT NULL,
            strike REAL NOT NULL,
            moneyness REAL NOT NULL,
            delta REAL NOT NULL,
            entry_spot REAL NOT NULL,
            entry_ltp REAL NOT NULL,
            quality_score INTEGER NOT NULL,
            call_wall REAL NOT NULL,
            put_wall REAL NOT NULL,
            gamma_flip REAL NOT NULL,
            dist_call_wall REAL NOT NULL,
            dist_put_wall REAL NOT NULL,
            dist_gamma_flip REAL NOT NULL,
            vanna REAL,
            charm REAL,
            dte REAL NOT NULL,
            india_vix REAL,
            pcr_oi REAL,
            session_vwap REAL,
            vwap_distance REAL,
            bar_delta REAL,
            cvd REAL,
            delta_ratio REAL,
            bias_score REAL NOT NULL,
            bias_regime TEXT NOT NULL,
            risk_reward_num REAL NOT NULL,
            target_distance_pts REAL NOT NULL,
            stop_distance_pts REAL NOT NULL,
            outcome_label TEXT,
            max_favorable_excursion_pct REAL,
            max_adverse_excursion_pct REAL,
            realized_rr REAL,
            holding_time_minutes REAL,
            market_hour_of_day INTEGER
        );
        """)
        conn.commit()


# In-memory fast cache for bookmarks and active state
_BOOKMARK_CACHE: dict[str, dict] = {}  # rec_id -> record_dict
_BOOKMARK_CACHE_LOCK = threading.Lock()
_CACHE_INITIALIZED = False


def _prime_cache() -> None:
    """Load active bookmarked records into in-memory cache once on startup."""
    global _CACHE_INITIALIZED
    if _CACHE_INITIALIZED:
        return
    ensure_schema()
    try:
        with _get_read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendation_records WHERE is_bookmarked = 1"
            ).fetchall()
            with _BOOKMARK_CACHE_LOCK:
                for r in rows:
                    _BOOKMARK_CACHE[r["rec_id"]] = dict(r)
        _CACHE_INITIALIZED = True
    except Exception as e:
        log.warning(f"Failed to prime bookmark cache: {e}")


def save_or_update_recommendation(idea_dict: dict, payload_json: str = "") -> str:
    """Save or update a recommendation record. Preserves existing bookmark status and triggered_at."""
    ensure_schema()
    rec_id = idea_dict["rec_id"]

    with _WRITE_LOCK, _get_write_conn() as conn:
        existing = conn.execute(
            "SELECT is_bookmarked, bookmarked_at, triggered_at, initial_score, current_score, initial_spot, initial_ltp FROM recommendation_records WHERE rec_id = ?",
            (rec_id,)
        ).fetchone()

        is_bm = existing["is_bookmarked"] if existing else int(idea_dict.get("is_bookmarked", 0))
        bm_at = existing["bookmarked_at"] if existing else idea_dict.get("bookmarked_at")

        new_trig = idea_dict.get("triggered_at") or _now_ist_str()
        if existing and existing["triggered_at"]:
            existing_trig = str(existing["triggered_at"])
            existing_date = existing_trig[:10]
            current_date = str(new_trig)[:10]
            is_fresh_day = (existing_date != current_date)
            prev_score = int(existing["current_score"] or existing["initial_score"] or 0)
            curr_score = int(idea_dict.get("quality_score", 0))
            was_previously_sub_threshold = (prev_score < 75 and curr_score >= 75)
            if is_fresh_day or was_previously_sub_threshold:
                trig_at = new_trig
                init_score = curr_score
                init_spot = float(idea_dict.get("current_spot", idea_dict.get("initial_spot", 0)))
                init_ltp = float(idea_dict.get("current_ltp", idea_dict.get("initial_ltp", 0)))
            else:
                trig_at = existing_trig
                init_score = existing["initial_score"]
                init_spot = float(existing["initial_spot"]) if existing["initial_spot"] is not None else float(idea_dict.get("initial_spot", 0))
                init_ltp = float(existing["initial_ltp"]) if existing["initial_ltp"] is not None else float(idea_dict.get("initial_ltp", 0))
        else:
            is_fresh_day = True
            trig_at = new_trig
            init_score = idea_dict.get("quality_score", 0)
            init_spot = float(idea_dict.get("initial_spot", idea_dict.get("current_spot", 0)))
            init_ltp = float(idea_dict.get("initial_ltp", idea_dict.get("current_ltp", 0)))

        conn.execute("""
            INSERT INTO recommendation_records (
                rec_id, symbol, side, strike, option_symbol, play_type, play_badge,
                triggered_at, initial_spot, initial_ltp, initial_score, current_score,
                conviction_tier, target_1, target_1_spot, target_2, target_2_spot,
                stop_loss, spot_invalidation, spot_invalidation_text, risk_reward_ratio,
                entry_trigger, rationale, catalyst_tags_json, is_bookmarked, bookmarked_at,
                status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rec_id) DO UPDATE SET
                triggered_at = excluded.triggered_at,
                initial_score = excluded.initial_score,
                initial_spot = excluded.initial_spot,
                initial_ltp = excluded.initial_ltp,
                option_symbol = excluded.option_symbol,
                current_score = excluded.current_score,
                conviction_tier = excluded.conviction_tier,
                target_1 = excluded.target_1,
                target_1_spot = excluded.target_1_spot,
                target_2 = excluded.target_2,
                target_2_spot = excluded.target_2_spot,
                stop_loss = excluded.stop_loss,
                spot_invalidation = excluded.spot_invalidation,
                spot_invalidation_text = excluded.spot_invalidation_text,
                risk_reward_ratio = excluded.risk_reward_ratio,
                entry_trigger = excluded.entry_trigger,
                rationale = excluded.rationale,
                catalyst_tags_json = excluded.catalyst_tags_json,
                payload_json = excluded.payload_json,
                status = CASE WHEN ? THEN 'ACTIVE' ELSE recommendation_records.status END,
                outcome_ts = CASE WHEN ? THEN NULL ELSE recommendation_records.outcome_ts END
        """, (
            rec_id,
            idea_dict["symbol"],
            idea_dict["side"],
            float(idea_dict["strike"]),
            idea_dict["option_symbol"],
            idea_dict["play_type"],
            idea_dict["play_badge"],
            trig_at,
            init_spot,
            init_ltp,
            int(init_score),
            int(idea_dict.get("quality_score", 0)),
            idea_dict.get("conviction_tier", "HIGH"),
            float(idea_dict["target_1"]),
            float(idea_dict.get("target_1_spot", 0)),
            float(idea_dict["target_2"]),
            float(idea_dict.get("target_2_spot", 0)),
            float(idea_dict["stop_loss"]),
            float(idea_dict["spot_invalidation"]),
            idea_dict.get("spot_invalidation_text", ""),
            idea_dict.get("risk_reward_ratio", "1 : 2.5"),
            idea_dict.get("entry_trigger", ""),
            idea_dict.get("rationale", ""),
            json.dumps(idea_dict.get("catalyst_tags", [])),
            is_bm,
            bm_at,
            idea_dict.get("status", "ACTIVE"),
            payload_json or json.dumps(idea_dict),
            int(is_fresh_day),
            int(is_fresh_day),
        ))
        conn.commit()

        # Update in-memory bookmark cache if bookmarked
        if is_bm:
            row = conn.execute("SELECT * FROM recommendation_records WHERE rec_id = ?", (rec_id,)).fetchone()
            if row:
                with _BOOKMARK_CACHE_LOCK:
                    _BOOKMARK_CACHE[rec_id] = dict(row)

        return trig_at


def toggle_bookmark(rec_id: str) -> bool:
    """Toggle bookmark status for a recommendation. Returns new bookmarked state (True/False)."""
    ensure_schema()
    _prime_cache()

    try:
        from analytics.trade_ideas import flush_telemetry
        flush_telemetry(rec_id=rec_id, timeout=0.5)
    except Exception:
        pass

    with _WRITE_LOCK, _get_write_conn() as conn:
        row = conn.execute("SELECT is_bookmarked FROM recommendation_records WHERE rec_id = ?", (rec_id,)).fetchone()
        if not row:
            symbol_guess = rec_id.split("_")[0] if "_" in rec_id else "NIFTY"
            now_ts = _now_ist_str()
            conn.execute(
                "INSERT INTO recommendation_records (rec_id, symbol, is_bookmarked, bookmarked_at) VALUES (?, ?, 1, ?)",
                (rec_id, symbol_guess, now_ts)
            )
            conn.commit()
            with _BOOKMARK_CACHE_LOCK:
                _BOOKMARK_CACHE[rec_id] = {"rec_id": rec_id, "symbol": symbol_guess, "is_bookmarked": 1, "bookmarked_at": now_ts}
            return True

        curr_bm = int(row["is_bookmarked"])
        new_bm = 0 if curr_bm else 1
        now_ts = _now_ist_str() if new_bm else None

        conn.execute(
            "UPDATE recommendation_records SET is_bookmarked = ?, bookmarked_at = ? WHERE rec_id = ?",
            (new_bm, now_ts, rec_id)
        )
        conn.commit()

        with _BOOKMARK_CACHE_LOCK:
            if new_bm:
                updated = conn.execute("SELECT * FROM recommendation_records WHERE rec_id = ?", (rec_id,)).fetchone()
                if updated:
                    _BOOKMARK_CACHE[rec_id] = dict(updated)
            else:
                _BOOKMARK_CACHE.pop(rec_id, None)

        return bool(new_bm)


def is_bookmarked(rec_id: str) -> bool:
    """Instant in-memory check if rec_id is bookmarked."""
    _prime_cache()
    with _BOOKMARK_CACHE_LOCK:
        return rec_id in _BOOKMARK_CACHE


def list_bookmarked_records(symbol: str | None = None) -> list[dict]:
    """Return all currently bookmarked recommendation records that have not crossed their expiry date."""
    _prime_cache()
    with _BOOKMARK_CACHE_LOCK:
        records = list(_BOOKMARK_CACHE.values())
    if symbol and symbol.upper() != "ALL":
        records = [r for r in records if r.get("symbol", "").upper() == symbol.upper()]
    # Keep bookmarked cards active until user explicitly unpins them OR contract crosses expiry date
    try:
        from data.pre_post_market import is_contract_expired
        records = [r for r in records if not is_contract_expired(r)]
    except Exception:
        pass
    return records


def append_score_history(
    rec_id: str,
    score: int,
    current_spot: float,
    current_ltp: float,
    reason: str,
    factors: dict | None = None,
    force_record: bool = False,
) -> None:
    """Append a score trajectory data point with reason explaining why conviction is increasing or fading.

    Suppresses entries outside active market hours and when spot & opt LTP have not changed from previous entry.
    """
    if not force_record:
        try:
            import config
            if not config.is_market_open(include_fno_extended=True):
                return
        except Exception:
            pass

    ensure_schema()
    now_ts = _now_ist_str()

    with _WRITE_LOCK, _get_write_conn() as conn:
        last = conn.execute(
            "SELECT score, current_spot, current_ltp, reason, factors_json FROM recommendation_score_history WHERE rec_id = ? ORDER BY history_id DESC LIMIT 1",
            (rec_id,)
        ).fetchone()

        if last is not None:
            if int(score) == int(last["score"]):
                # No change in score: do not append duplicate trajectory row
                return

            last_spot = float(last["current_spot"] or 0)
            last_ltp = float(last["current_ltp"] or 0)
            if not force_record and abs(current_spot - last_spot) < 0.05 and abs(current_ltp - last_ltp) < 0.05:
                # No change in spot and opt LTP from previous entry: do not append duplicate trajectory row
                return

            score_delta = int(score) - int(last["score"])

            # Attributing factors that strengthened or faded the score
            prev_factors = {}
            if last["factors_json"]:
                try:
                    prev_factors = json.loads(last["factors_json"])
                except Exception:
                    pass

            curr_factors = factors or {}
            attr_parts = []

            # 1. Bias Score delta attribution
            prev_bias = prev_factors.get("bias_score")
            curr_bias = curr_factors.get("bias_score")
            if prev_bias is not None and curr_bias is not None and abs(curr_bias - prev_bias) >= 0.2:
                b_diff = curr_bias - prev_bias
                if score_delta > 0:
                    attr_parts.append(f"Directional bias strengthened {b_diff:+.1f} (from {prev_bias:+.1f} to {curr_bias:+.1f})")
                else:
                    attr_parts.append(f"Directional bias softened {b_diff:+.1f} (from {prev_bias:+.1f} to {curr_bias:+.1f})")

            # 2. Underlying Spot movement attribution
            prev_spot = float(last["current_spot"] or 0)
            if prev_spot > 0 and abs(current_spot - prev_spot) >= 5.0:
                spot_diff = current_spot - prev_spot
                if score_delta > 0:
                    attr_parts.append(f"Spot moved {spot_diff:+.0f} pts favorably (₹{prev_spot:,.0f} → ₹{current_spot:,.0f})")
                else:
                    attr_parts.append(f"Spot rotated {spot_diff:+.0f} pts adversely (₹{prev_spot:,.0f} → ₹{current_spot:,.0f})")

            # 3. Delta ratio / order flow shift
            prev_dr = prev_factors.get("delta_ratio")
            curr_dr = curr_factors.get("delta_ratio")
            if prev_dr is not None and curr_dr is not None and abs(curr_dr - prev_dr) >= 0.05:
                if score_delta > 0:
                    attr_parts.append(f"OrderFlow delta aggression expanded ({curr_dr:+.2f})")
                else:
                    attr_parts.append(f"OrderFlow momentum decelerated ({curr_dr:+.2f})")

            # Combine with strategy-specific reason
            delta_tag = f"Conviction strengthened (+{score_delta})" if score_delta > 0 else f"Conviction faded ({score_delta})"
            if attr_parts:
                final_reason = f"{delta_tag}: " + " · ".join(attr_parts) + f" · {reason}"
            else:
                final_reason = f"{delta_tag} · {reason}"
        else:
            score_delta = 0
            final_reason = reason

        conn.execute("""
            INSERT INTO recommendation_score_history (
                rec_id, recorded_at, score, score_delta, current_spot, current_ltp, reason, factors_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rec_id, now_ts, int(score), int(score_delta),
            float(current_spot), float(current_ltp),
            final_reason, json.dumps(factors or {})
        ))
        conn.commit()


def get_score_history(rec_id: str, limit: int = 20) -> list[dict]:
    """Retrieve score evolution log for rec_id."""
    ensure_schema()
    try:
        with _get_read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM recommendation_score_history WHERE rec_id = ? ORDER BY history_id DESC LIMIT ?",
                (rec_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning(f"Failed to get score history for {rec_id}: {e}")
        return []


def record_event(
    rec_id: str,
    symbol: str,
    event_type: str,
    spot_at_event: float,
    ltp_at_event: float,
    pnl_pct: float,
    message: str,
    was_bookmarked: bool,
) -> int:
    """Record a lifecycle event (TGT1_HIT, TGT2_HIT, SL_HIT, INVALIDATED). Returns event_id."""
    ensure_schema()
    now_ts = _now_ist_str()

    with _WRITE_LOCK, _get_write_conn() as conn:
        # Check if identical event already recorded in last 30 minutes
        existing = conn.execute(
            "SELECT event_id FROM recommendation_events WHERE rec_id = ? AND event_type = ?",
            (rec_id, event_type)
        ).fetchone()
        if existing:
            return existing["event_id"]

        cur = conn.execute("""
            INSERT INTO recommendation_events (
                rec_id, symbol, event_type, event_ts, spot_at_event, ltp_at_event, pnl_pct, message, was_bookmarked
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rec_id, symbol, event_type, now_ts,
            float(spot_at_event), float(ltp_at_event), float(pnl_pct),
            message, 1 if was_bookmarked else 0
        ))

        # Update recommendation record status
        status_map = {
            "TGT1_HIT": "TGT1_HIT",
            "TGT2_HIT": "TGT2_HIT",
            "SL_HIT": "SL_HIT",
            "INVALIDATED": "INVALIDATED",
        }
        rec_status = status_map.get(event_type, "ACTIVE")
        conn.execute(
            "UPDATE recommendation_records SET status = ?, outcome_ts = ? WHERE rec_id = ?",
            (rec_status, now_ts, rec_id)
        )
        conn.commit()

        # Update in-memory cache if bookmarked
        if was_bookmarked and rec_id in _BOOKMARK_CACHE:
            with _BOOKMARK_CACHE_LOCK:
                _BOOKMARK_CACHE[rec_id]["status"] = rec_status
                _BOOKMARK_CACHE[rec_id]["outcome_ts"] = now_ts

        # Update ML telemetry dataset with outcome label and performance
        try:
            date_tag = now_ts[:10].replace("-", "")
            for s_id in (f"{rec_id}_{date_tag}", rec_id):
                update_ml_outcome(
                    sample_id=s_id,
                    outcome_label=rec_status,
                    max_favorable_excursion_pct=max(0.0, float(pnl_pct)),
                    max_adverse_excursion_pct=min(0.0, float(pnl_pct)) if float(pnl_pct) < 0 else 0.0,
                    realized_rr=round(abs(float(pnl_pct)) / 25.0, 2),
                    holding_time_minutes=30.0,
                )
        except Exception:
            pass

        return cur.lastrowid


def resolve_live_option_ltp(
    symbol: str,
    strike: float,
    side: str,
    option_symbol: str,
    options_ltp_map: dict[str, float] | None,
) -> float | None:
    """Premium used to close a recommendation. A missing quote stays missing.

    Target and stop checks must not invent a premium from a 0.50 delta. Spot
    invalidation is a separate check and does not use this value.
    """
    book = options_ltp_map or {}
    for key in (option_symbol, f"{strike}_{side}"):
        if not key:
            continue
        try:
            px = float(book.get(key) or 0.0)
        except (TypeError, ValueError):
            px = 0.0
        if px > 0:
            return px
    try:
        from app.spot_ticker import get_option_ws_quote
        quote = get_option_ws_quote(symbol, strike, side, max_age_s=120.0)
    except Exception:
        quote = None
    if not quote:
        return None
    try:
        px = float(quote.get("ltp") or 0.0)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def evaluate_recommendation_lifecycle(
    symbol: str,
    current_spot: float,
    options_ltp_map: dict[str, float] | None = None,
    session_date: str | None = None,
) -> list[dict]:
    """Check active recommendations for symbol against live spot & option LTPs, triggering events when met."""
    if current_spot <= 0:
        return []

    ensure_schema()
    options_ltp_map = options_ltp_map or {}
    events_triggered = []
    trade_date = session_date or datetime.now(IST).strftime("%Y-%m-%d")

    with _get_read_conn() as conn:
        active_recs = conn.execute(
            "SELECT * FROM recommendation_records WHERE symbol = ? AND status = 'ACTIVE' AND substr(triggered_at, 1, 10) = ?",
            (symbol.upper(), trade_date)
        ).fetchall()

    for r in active_recs:
        rec_id = r["rec_id"]
        side = r["side"]
        strike = float(r["strike"])
        init_ltp = float(r["initial_ltp"] or 0.0)
        t1 = float(r["target_1"] or 0.0)
        t2 = float(r["target_2"] or 0.0)
        sl = float(r["stop_loss"] or 0.0)
        inval = float(r["spot_invalidation"] or 0.0)
        is_bm = bool(r["is_bookmarked"])

        opt_sym = r["option_symbol"] or ""
        curr_ltp = resolve_live_option_ltp(
            symbol, strike, side, opt_sym, options_ltp_map,
        )

        event_type = None
        event_msg = ""

        # Check spot invalidation
        if inval > 0:
            if side == "CE" and current_spot <= inval:
                event_type = "INVALIDATED"
                event_msg = f"🛑 Spot Invalidation Breached at ₹{current_spot:,.1f} (Inval level: ₹{inval:,.0f})"
            elif side == "PE" and current_spot >= inval:
                event_type = "INVALIDATED"
                event_msg = f"🛑 Spot Invalidation Breached at ₹{current_spot:,.1f} (Inval level: ₹{inval:,.0f})"

        # Check price targets and stop loss only against a live premium.
        if not event_type and curr_ltp is not None and curr_ltp > 0:
            pnl_pct = round(((curr_ltp - init_ltp) / init_ltp) * 100.0, 1) if init_ltp > 0 else 0.0
            if t2 > 0 and curr_ltp >= t2:
                event_type = "TGT2_HIT"
                event_msg = f"🚀 Target 2 Achieved at ₹{curr_ltp:,.1f} (+{pnl_pct:+.0f}%)"
            elif t1 > 0 and curr_ltp >= t1:
                event_type = "TGT1_HIT"
                event_msg = f"🎯 Target 1 Achieved at ₹{curr_ltp:,.1f} (+{pnl_pct:+.0f}%)"
            elif sl > 0 and curr_ltp <= sl:
                event_type = "SL_HIT"
                event_msg = f"⛔ Stop Loss Hit at ₹{curr_ltp:,.1f} ({pnl_pct:+.0f}%)"

        if event_type:
            live_px = float(curr_ltp or init_ltp or 0.0)
            pnl_pct = round(((live_px - init_ltp) / init_ltp) * 100.0, 1) if init_ltp > 0 else 0.0
            try:
                record_event(
                    rec_id=rec_id,
                    symbol=symbol.upper(),
                    event_type=event_type,
                    spot_at_event=current_spot,
                    ltp_at_event=live_px,
                    pnl_pct=pnl_pct,
                    message=event_msg,
                    was_bookmarked=is_bm,
                )
                events_triggered.append({
                    "rec_id": rec_id,
                    "event_type": event_type,
                    "message": event_msg,
                    "pnl_pct": pnl_pct,
                })
            except Exception as e_rec:
                log.debug("Error recording lifecycle event for %s: %s", rec_id, e_rec)

    return events_triggered


def get_unacknowledged_alerts(only_bookmarked: bool = True) -> list[dict]:
    """Retrieve unacknowledged events for audio chimes and alert toasts."""
    ensure_schema()
    try:
        with _get_read_conn() as conn:
            query = """
                SELECT * FROM recommendation_events
                WHERE alert_acknowledged = 0
            """
            if only_bookmarked:
                query += " AND was_bookmarked = 1"
            query += " ORDER BY event_id ASC LIMIT 10"

            rows = conn.execute(query).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning(f"Failed to fetch unacknowledged alerts: {e}")
        return []


def acknowledge_alerts(event_ids: list[int]) -> None:
    """Mark events as acknowledged after toast and audio chime have fired."""
    if not event_ids:
        return
    ensure_schema()
    with _WRITE_LOCK, _get_write_conn() as conn:
        placeholders = ",".join("?" * len(event_ids))
        conn.execute(
            f"UPDATE recommendation_events SET alert_acknowledged = 1 WHERE event_id IN ({placeholders})",
            event_ids
        )
        conn.commit()


def log_ml_features(feat_dict: dict) -> None:
    """Store quantitative point-in-time features for future AI/ML modeling."""
    ensure_schema()
    with _WRITE_LOCK, _get_write_conn() as conn:
        conn.execute("""
            INSERT INTO ml_features_dataset (
                sample_id, symbol, triggered_at, strategy_name, side, strike, moneyness,
                delta, entry_spot, entry_ltp, quality_score, call_wall, put_wall, gamma_flip,
                dist_call_wall, dist_put_wall, dist_gamma_flip, vanna, charm, dte, india_vix,
                pcr_oi, session_vwap, vwap_distance, bar_delta, cvd, delta_ratio, bias_score,
                bias_regime, risk_reward_num, target_distance_pts, stop_distance_pts, market_hour_of_day
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sample_id) DO NOTHING
        """, (
            feat_dict["sample_id"], feat_dict["symbol"], feat_dict["triggered_at"],
            feat_dict["strategy_name"], feat_dict["side"], float(feat_dict["strike"]),
            float(feat_dict["moneyness"]), float(feat_dict["delta"]), float(feat_dict["entry_spot"]),
            float(feat_dict["entry_ltp"]), int(feat_dict["quality_score"]),
            float(feat_dict["call_wall"]), float(feat_dict["put_wall"]), float(feat_dict["gamma_flip"]),
            float(feat_dict["dist_call_wall"]), float(feat_dict["dist_put_wall"]), float(feat_dict["dist_gamma_flip"]),
            feat_dict.get("vanna"), feat_dict.get("charm"), float(feat_dict.get("dte", 3.0)),
            feat_dict.get("india_vix"), feat_dict.get("pcr_oi"), float(feat_dict.get("session_vwap", 0)),
            float(feat_dict.get("vwap_distance", 0)), float(feat_dict.get("bar_delta", 0)),
            float(feat_dict.get("cvd", 0)), float(feat_dict.get("delta_ratio", 0)),
            float(feat_dict["bias_score"]), feat_dict["bias_regime"], float(feat_dict.get("risk_reward_num", 2.5)),
            float(feat_dict["target_distance_pts"]), float(feat_dict["stop_distance_pts"]),
            int(feat_dict.get("market_hour_of_day", datetime.now(IST).hour))
        ))
        conn.commit()


def update_ml_outcome(
    sample_id: str,
    outcome_label: str,
    max_favorable_excursion_pct: float,
    max_adverse_excursion_pct: float,
    realized_rr: float,
    holding_time_minutes: float,
) -> None:
    """Update target outcome labels on recommendation completion for ML training ground-truth."""
    ensure_schema()
    with _WRITE_LOCK, _get_write_conn() as conn:
        conn.execute("""
            UPDATE ml_features_dataset SET
                outcome_label = ?,
                max_favorable_excursion_pct = ?,
                max_adverse_excursion_pct = ?,
                realized_rr = ?,
                holding_time_minutes = ?
            WHERE sample_id = ?
        """, (
            outcome_label, float(max_favorable_excursion_pct),
            float(max_adverse_excursion_pct), float(realized_rr),
            float(holding_time_minutes), sample_id
        ))
        conn.commit()


def get_strategy_effectiveness_metrics(symbol: str | None = None) -> list[dict]:
    """Compute empirical effectiveness metrics across the 7 institutional strategies."""
    ensure_schema()
    strategies = [
        ("MOMENTUM", "ATM Momentum Play (Δ ~0.50)"),
        ("STRUCTURE", "Institutional Structure Play (Δ ~0.40)"),
        ("STRIKE BREAK", "Convex break at a gamma strike (Δ ~0.28)"),
        ("ICEBERG FADE", "OrderFlow Iceberg Absorption Fade (Δ ~0.50)"),
        ("VANNA-CHARM", "0DTE Vanna-Charm Compression (Δ ~0.20)"),
        ("VOL DISPERSION", "Vol Dispersion / Long Gamma Blast (Δ ~0.50)"),
        ("RATIO REVERSAL", "Zero-Cost Ratio Risk Reversal (Δ ~0.40)"),
    ]

    results = []
    try:
        with _get_read_conn() as conn:
            where_clause = "WHERE symbol = ?" if (symbol and symbol.upper() != "ALL") else ""
            params = (symbol.upper(),) if (symbol and symbol.upper() != "ALL") else ()

            for badge, name in strategies:
                badge_where = f"{where_clause} {'AND' if where_clause else 'WHERE'} play_badge = ?"
                b_params = params + (badge,)

                total = conn.execute(
                    f"SELECT COUNT(*) FROM recommendation_records {badge_where}", b_params
                ).fetchone()[0]

                t1_hits = conn.execute(
                    f"SELECT COUNT(*) FROM recommendation_records {badge_where} AND status IN ('TGT1_HIT', 'TGT2_HIT')", b_params
                ).fetchone()[0]

                t2_hits = conn.execute(
                    f"SELECT COUNT(*) FROM recommendation_records {badge_where} AND status = 'TGT2_HIT'", b_params
                ).fetchone()[0]

                sl_hits = conn.execute(
                    f"SELECT COUNT(*) FROM recommendation_records {badge_where} AND status = 'SL_HIT'", b_params
                ).fetchone()[0]

                invals = conn.execute(
                    f"SELECT COUNT(*) FROM recommendation_records {badge_where} AND status = 'INVALIDATED'", b_params
                ).fetchone()[0]

                t1_rate = round((t1_hits / total) * 100, 1) if total > 0 else 0.0
                t2_rate = round((t2_hits / total) * 100, 1) if total > 0 else 0.0
                sl_rate = round((sl_hits / total) * 100, 1) if total > 0 else 0.0
                inv_rate = round((invals / total) * 100, 1) if total > 0 else 0.0
                avg_rr = None
                if total > 0:
                    # realized_rr is written on the ML row, not recommendation_records.
                    if symbol and symbol.upper() != "ALL":
                        rr_sql = (
                            "SELECT AVG(realized_rr) FROM ml_features_dataset "
                            "WHERE symbol = ? AND strategy_name = ? AND realized_rr IS NOT NULL"
                        )
                        rr_params = (symbol.upper(), badge)
                    else:
                        rr_sql = (
                            "SELECT AVG(realized_rr) FROM ml_features_dataset "
                            "WHERE strategy_name = ? AND realized_rr IS NOT NULL"
                        )
                        rr_params = (badge,)
                    rr_row = conn.execute(rr_sql, rr_params).fetchone()
                    if rr_row and rr_row[0] is not None:
                        avg_rr = round(float(rr_row[0]), 2)
                results.append({
                    "badge": badge,
                    "name": name,
                    "total": total,
                    "t1_hits": t1_hits,
                    "t1_rate": t1_rate,
                    "t2_hits": t2_hits,
                    "t2_rate": t2_rate,
                    "sl_hits": sl_hits,
                    "sl_rate": sl_rate,
                    "invals": invals,
                    "inv_rate": inv_rate,
                    "avg_rr": avg_rr,
                    "win_loss": round(t1_hits / max(1, sl_hits + invals), 2) if total else 0.0,
                })
    except Exception as e:
        log.warning(f"Error computing strategy effectiveness: {e}")

    return results


def summarize_effectiveness(rows: list[dict] | None) -> dict:
    """Roll strategy rows into one strip. Rates stay 0 when nothing has been tracked."""
    items = list(rows or [])
    total = sum(int(m.get("total") or 0) for m in items)
    t1 = sum(int(m.get("t1_hits") or 0) for m in items)
    sl = sum(int(m.get("sl_hits") or 0) for m in items)
    inv = sum(int(m.get("invals") or 0) for m in items)
    rr_vals = [float(m["avg_rr"]) for m in items if isinstance(m.get("avg_rr"), (int, float))]
    avg_rr = round(sum(rr_vals) / len(rr_vals), 2) if rr_vals else None
    return {
        "total_tracked": total,
        "target_hit_rate": round(t1 / total * 100.0, 1) if total else 0.0,
        "stop_loss_rate": round(sl / total * 100.0, 1) if total else 0.0,
        "spot_invalidation_rate": round(inv / total * 100.0, 1) if total else 0.0,
        "avg_realized_rr": avg_rr,
        "has_samples": total > 0,
        "by_strategy": items,
    }
