"""GO decision efficiency ledger (Phase 0/1 spine for Epic H).

Records emit / stage / skip events with immutable decision snapshots.
Tracks open staged positions for trail + MFE/MAE mark updates.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = ROOT / "data" / "journal" / "go_efficiency.db"
_LOCK = threading.Lock()


def _now_ist() -> str:
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS go_events (
            event_id      TEXT PRIMARY KEY,
            decision_id   TEXT NOT NULL,
            symbol        TEXT NOT NULL,
            mode          TEXT NOT NULL,
            state         TEXT NOT NULL,
            score         REAL,
            strategy_key  TEXT,
            event_type    TEXT NOT NULL,
            trade_id      TEXT,
            ts            TEXT NOT NULL,
            decision_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS go_open (
            decision_id     TEXT PRIMARY KEY,
            trade_id        TEXT,
            symbol          TEXT NOT NULL,
            mode            TEXT NOT NULL,
            strategy_key    TEXT,
            entry_premium   REAL,
            entry_spot      REAL,
            mark_premium    REAL,
            mark_spot       REAL,
            mfe             REAL NOT NULL DEFAULT 0,
            mae             REAL NOT NULL DEFAULT 0,
            trail_armed     INTEGER NOT NULL DEFAULT 0,
            trail_stop      REAL,
            status          TEXT NOT NULL DEFAULT 'OPEN',
            opened_at       TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            decision_json   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_go_events_sym ON go_events (symbol, ts DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_go_events_dec ON go_events (decision_id, event_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_go_open_sym ON go_open (symbol, status)"
    )
    conn.commit()
    return conn


def ensure_schema() -> None:
    with _LOCK:
        c = _conn()
        c.close()


def record_event(
    event_type: str,
    decision: dict,
    *,
    trade_id: str | None = None,
) -> str:
    """Append EMIT / STAGE / SKIP. STAGE also opens a go_open row."""
    ensure_schema()
    et = str(event_type or "").upper()
    if et not in ("EMIT", "STAGE", "SKIP"):
        raise ValueError(f"invalid event_type {event_type!r}")
    event_id = f"ge_{uuid.uuid4().hex[:12]}"
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT INTO go_events (
                    event_id, decision_id, symbol, mode, state, score,
                    strategy_key, event_type, trade_id, ts, decision_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    str(decision.get("id") or ""),
                    str(decision.get("symbol") or "").upper(),
                    str(decision.get("mode") or ""),
                    str(decision.get("state") or ""),
                    float(decision.get("score") or 0),
                    str(decision.get("strategy_key") or ""),
                    et,
                    trade_id,
                    _now_ist(),
                    json.dumps(decision, default=str),
                ),
            )
            if et == "STAGE":
                entry_prem = _entry_premium(decision)
                entry_spot = float((decision.get("context") or {}).get("spot") or 0)
                now = _now_ist()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO go_open (
                        decision_id, trade_id, symbol, mode, strategy_key,
                        entry_premium, entry_spot, mark_premium, mark_spot,
                        mfe, mae, trail_armed, trail_stop, status,
                        opened_at, updated_at, decision_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(decision.get("id") or ""),
                        trade_id,
                        str(decision.get("symbol") or "").upper(),
                        str(decision.get("mode") or ""),
                        str(decision.get("strategy_key") or ""),
                        entry_prem,
                        entry_spot,
                        entry_prem,
                        entry_spot,
                        0.0,
                        0.0,
                        0,
                        None,
                        "OPEN",
                        now,
                        now,
                        json.dumps(decision, default=str),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    return event_id


def _entry_premium(decision: dict) -> float | None:
    """Net premium points: debit negative for longs, credit positive for shorts."""
    risk = decision.get("risk") or {}
    if risk.get("credit_or_debit") is not None:
        return float(risk["credit_or_debit"])
    contracts = decision.get("contracts") or []
    if not contracts:
        return None
    net = 0.0
    priced = False
    for c in contracts:
        if c.get("ltp") is None:
            continue
        priced = True
        px = float(c["ltp"])
        net += px if str(c.get("action")).upper() == "SELL" else -px
    return round(net, 2) if priced else None


def mark_open(
    decision_id: str,
    *,
    mark_premium: float,
    mark_spot: float | None = None,
    trail: dict | None = None,
) -> dict | None:
    """Update mark, MFE/MAE, sticky trail for an OPEN staged GO.

    Trail stickiness: once armed, ``trail_stop`` stays set even if mark
    retreats below the arm threshold. Hit → status TRAIL_STOP.
    """
    ensure_schema()
    with _LOCK:
        conn = _conn()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM go_open WHERE decision_id = ? AND status = 'OPEN'",
                (decision_id,),
            ).fetchone()
            if row is None:
                return None
            entry = float(row["entry_premium"] or 0)
            # Signed net (SELL−BUY): long debit negative, short credit positive.
            # Favorable for both = entry − mark (long LTP↑ → mark more negative;
            # short premium↓ → mark smaller credit).
            pnl = entry - float(mark_premium)
            fav = max(pnl, 0.0)
            adv = max(-pnl, 0.0)
            mfe = max(float(row["mfe"] or 0), fav)
            mae = max(float(row["mae"] or 0), adv)
            trail_armed = int(row["trail_armed"] or 0)
            trail_stop = row["trail_stop"]
            status = row["status"]

            # Sticky directional trail from abs premiums when not supplied
            if trail is None and str(row["mode"] or "") == "directional" and entry != 0:
                from analytics.go_desk import evaluate_trail

                entry_abs = abs(entry)
                mark_abs = abs(float(mark_premium))
                fresh = evaluate_trail(entry_ltp=entry_abs, mark_ltp=mark_abs)
                if trail_armed or fresh.get("armed"):
                    trail_armed = 1
                    if trail_stop is None and fresh.get("trail_stop") is not None:
                        trail_stop = float(fresh["trail_stop"])
                    elif fresh.get("trail_stop") is not None and trail_stop is not None:
                        # Keep the original lock once armed (sticky)
                        trail_stop = float(trail_stop)
                    if trail_stop is not None and mark_abs <= float(trail_stop):
                        status = "TRAIL_STOP"
                trail = {
                    "armed": bool(trail_armed),
                    "trail_stop": trail_stop,
                    "stopped": status == "TRAIL_STOP",
                    "pnl_pct": fresh.get("pnl_pct"),
                }
            elif trail:
                trail_armed = 1 if trail.get("armed") else trail_armed
                if trail.get("trail_stop") is not None and not trail_armed:
                    trail_stop = float(trail["trail_stop"])
                elif trail.get("trail_stop") is not None and trail_stop is None:
                    trail_stop = float(trail["trail_stop"])
                if trail.get("armed"):
                    trail_armed = 1
                    if trail_stop is None and trail.get("trail_stop") is not None:
                        trail_stop = float(trail["trail_stop"])
                if trail.get("stopped"):
                    status = "TRAIL_STOP"

            now = _now_ist()
            conn.execute(
                """
                UPDATE go_open SET
                    mark_premium = ?, mark_spot = ?, mfe = ?, mae = ?,
                    trail_armed = ?, trail_stop = ?, status = ?, updated_at = ?
                WHERE decision_id = ?
                """,
                (
                    float(mark_premium),
                    mark_spot if mark_spot is not None else row["mark_spot"],
                    round(mfe, 2),
                    round(mae, 2),
                    trail_armed,
                    trail_stop,
                    status,
                    now,
                    decision_id,
                ),
            )
            conn.commit()
            return {
                "decision_id": decision_id,
                "mark_premium": float(mark_premium),
                "mfe": round(mfe, 2),
                "mae": round(mae, 2),
                "trail_armed": bool(trail_armed),
                "trail_stop": trail_stop,
                "status": status,
                "trail": trail,
            }
        finally:
            conn.close()


def list_marked(symbol: str | None = None) -> list[dict[str, Any]]:
    """Open + closed/trail rows for backtest-style MFE/MAE slices."""
    ensure_schema()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        if symbol:
            rows = conn.execute(
                "SELECT strategy_key, mode, mfe, mae, status, decision_id FROM go_open WHERE symbol = ?",
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT strategy_key, mode, mfe, mae, status, decision_id FROM go_open"
            ).fetchall()
        return [
            {
                "strategy_key": r["strategy_key"],
                "mode": r["mode"],
                "mfe": r["mfe"],
                "mae": r["mae"],
                "status": r["status"],
                "decision_id": r["decision_id"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def close_open(decision_id: str, status: str = "CLOSED") -> None:
    ensure_schema()
    with _LOCK:
        conn = _conn()
        try:
            conn.execute(
                "UPDATE go_open SET status = ?, updated_at = ? WHERE decision_id = ?",
                (status, _now_ist(), decision_id),
            )
            conn.commit()
        finally:
            conn.close()


def list_open(symbol: str | None = None, *, include_decision: bool = False) -> list[dict[str, Any]]:
    ensure_schema()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM go_open WHERE status = 'OPEN' AND symbol = ? ORDER BY opened_at DESC",
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM go_open WHERE status = 'OPEN' ORDER BY opened_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            item = {
                "decision_id": r["decision_id"],
                "trade_id": r["trade_id"],
                "symbol": r["symbol"],
                "mode": r["mode"],
                "strategy_key": r["strategy_key"],
                "entry_premium": r["entry_premium"],
                "entry_spot": r["entry_spot"],
                "mark_premium": r["mark_premium"],
                "mark_spot": r["mark_spot"],
                "mfe": r["mfe"],
                "mae": r["mae"],
                "trail_armed": bool(r["trail_armed"]),
                "trail_stop": r["trail_stop"],
                "status": r["status"],
                "opened_at": r["opened_at"],
                "updated_at": r["updated_at"],
            }
            if include_decision:
                try:
                    item["decision"] = json.loads(r["decision_json"] or "{}")
                except Exception:
                    item["decision"] = {}
            out.append(item)
        return out
    finally:
        conn.close()


def summarize(symbol: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Descriptive counts for Scorecard / Efficiency tab."""
    ensure_schema()
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM go_events WHERE symbol = ? ORDER BY ts DESC LIMIT ?",
                (symbol.upper(), limit),
            ).fetchall()
            open_rows = conn.execute(
                "SELECT mfe, mae, trail_armed, status FROM go_open WHERE symbol = ?",
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM go_events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            open_rows = conn.execute(
                "SELECT mfe, mae, trail_armed, status FROM go_open"
            ).fetchall()
    finally:
        conn.close()

    counts = {"EMIT": 0, "STAGE": 0, "SKIP": 0}
    by_state = {"FIRE": 0, "SCOUT": 0, "HOLD": 0}
    for r in rows:
        et = r["event_type"]
        if et in counts:
            counts[et] += 1
        if et == "EMIT":
            st = r["state"]
            if st in by_state:
                by_state[st] += 1

    staged = counts["STAGE"]
    skipped = counts["SKIP"]
    emitted_fire = by_state["FIRE"]
    mfes = [float(r["mfe"] or 0) for r in open_rows if r["status"] in ("OPEN", "TRAIL_STOP", "CLOSED")]
    maes = [float(r["mae"] or 0) for r in open_rows if r["status"] in ("OPEN", "TRAIL_STOP", "CLOSED")]
    avg_mfe = round(sum(mfes) / len(mfes), 2) if mfes else None
    avg_mae = round(sum(maes) / len(maes), 2) if maes else None
    edge = round(avg_mfe / avg_mae, 2) if avg_mfe is not None and avg_mae and avg_mae > 0 else None

    # H3 / H5 slices: by mode × state, hour-of-day, DOW, lunch cohort
    by_mode: dict[str, dict[str, int]] = {}
    by_strategy: dict[str, int] = {}
    by_hour: dict[str, dict[str, int]] = {}
    by_dow: dict[str, dict[str, int]] = {}
    lunch_emits = 0
    lunch_stages = 0
    emit_stage_pairs: list[float] = []
    emit_ts: dict[str, str] = {}
    stage_ts: dict[str, str] = {}
    trail_armed_n = sum(1 for r in open_rows if int(r["trail_armed"] or 0))
    complete_evidence = 0
    emit_with_json = 0
    _dow_names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    def _bucket_ts(ts_raw: str) -> tuple[str | None, str | None]:
        try:
            from datetime import datetime as _dt
            t = _dt.fromisoformat(str(ts_raw))
            return f"{t.hour:02d}", _dow_names[t.weekday()]
        except Exception:
            try:
                hour = str(ts_raw).split("T")[1][:2]
                return hour, None
            except Exception:
                return None, None

    for r in rows:
        mode = r["mode"] or "unknown"
        st = r["state"] or "HOLD"
        by_mode.setdefault(mode, {"EMIT": 0, "STAGE": 0, "SKIP": 0, "FIRE": 0, "SCOUT": 0, "HOLD": 0})
        et = r["event_type"]
        if et in by_mode[mode]:
            by_mode[mode][et] += 1
        if et == "EMIT" and st in by_mode[mode]:
            by_mode[mode][st] += 1
        sk = r["strategy_key"] or "—"
        hour_key, dow_key = _bucket_ts(r["ts"])
        if hour_key:
            by_hour.setdefault(hour_key, {"EMIT": 0, "STAGE": 0, "SKIP": 0, "FIRE": 0})
            if et in by_hour[hour_key]:
                by_hour[hour_key][et] += 1
            if et == "EMIT" and st == "FIRE":
                by_hour[hour_key]["FIRE"] += 1
        if dow_key:
            by_dow.setdefault(dow_key, {"EMIT": 0, "STAGE": 0, "SKIP": 0, "FIRE": 0})
            if et in by_dow[dow_key]:
                by_dow[dow_key][et] += 1
            if et == "EMIT" and st == "FIRE":
                by_dow[dow_key]["FIRE"] += 1
        if et == "EMIT":
            by_strategy[sk] = by_strategy.get(sk, 0) + 1
            emit_ts[r["decision_id"]] = r["ts"]
            try:
                dj = json.loads(r["decision_json"] or "{}")
                emit_with_json += 1
                ev = dj.get("evidence") or []
                if ev and all(("pass" in e or "pass_" in e) for e in ev):
                    complete_evidence += 1
                soft = dj.get("soft_flags") or []
                if "lunch_chop" in soft:
                    lunch_emits += 1
            except Exception:
                pass
        if et == "STAGE":
            stage_ts[r["decision_id"]] = r["ts"]
            try:
                dj = json.loads(r["decision_json"] or "{}")
                if "lunch_chop" in (dj.get("soft_flags") or []):
                    lunch_stages += 1
            except Exception:
                pass

    for did, sts in stage_ts.items():
        ets = emit_ts.get(did)
        if not ets:
            continue
        try:
            # naive seconds from ISO without full parse deps
            from datetime import datetime as _dt
            t0 = _dt.fromisoformat(ets)
            t1 = _dt.fromisoformat(sts)
            emit_stage_pairs.append(max(0.0, (t1 - t0).total_seconds()))
        except Exception:
            pass

    emit_stage_pairs.sort()
    median_emit_to_stage = None
    if emit_stage_pairs:
        mid = len(emit_stage_pairs) // 2
        if len(emit_stage_pairs) % 2:
            median_emit_to_stage = round(emit_stage_pairs[mid], 1)
        else:
            median_emit_to_stage = round(
                (emit_stage_pairs[mid - 1] + emit_stage_pairs[mid]) / 2.0, 1
            )

    return {
        "events": len(rows),
        "counts": counts,
        "emit_by_state": by_state,
        "stage_rate_vs_fire_emit": (
            round(staged / emitted_fire, 3) if emitted_fire else None
        ),
        "skip_rate_vs_fire_emit": (
            round(skipped / emitted_fire, 3) if emitted_fire else None
        ),
        "open_positions": sum(1 for r in open_rows if r["status"] == "OPEN"),
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "edge_ratio": edge,
        "by_mode": by_mode,
        "by_strategy_key": dict(sorted(by_strategy.items(), key=lambda x: -x[1])[:20]),
        "by_hour_emit": {h: v.get("EMIT", 0) for h, v in sorted(by_hour.items())},
        "by_hour": dict(sorted(by_hour.items())),
        "by_dow": {d: by_dow[d] for d in _dow_names if d in by_dow},
        "lunch_emit_count": lunch_emits,
        "lunch_stage_count": lunch_stages,
        "process": {
            "median_emit_to_stage_sec": median_emit_to_stage,
            "trail_armed_open": trail_armed_n,
            "evidence_complete_pct": (
                round(100.0 * complete_evidence / emit_with_json, 1)
                if emit_with_json else None
            ),
        },
        "recent": [
            {
                "event_id": r["event_id"],
                "decision_id": r["decision_id"],
                "symbol": r["symbol"],
                "mode": r["mode"],
                "state": r["state"],
                "score": r["score"],
                "strategy_key": r["strategy_key"],
                "event_type": r["event_type"],
                "trade_id": r["trade_id"],
                "ts": r["ts"],
            }
            for r in rows[:50]
        ],
    }
