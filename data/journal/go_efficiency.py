"""GO decision efficiency ledger (Phase 0/1 spine for Epic H).

Records emit / stage / skip events with immutable decision snapshots.
Tracks open staged positions for trail + MFE/MAE mark updates.
Settles cost-aware ₹ P&L on close / TRAIL_STOP (same model as journal store).
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

# Same cost model as data.journal.store (H8) — VERIFY vs broker schedule.
DEFAULT_BROKERAGE_PER_ORDER_INR = 20.0
DEFAULT_SLIPPAGE_PTS_PER_LEG = 0.50

_GO_OPEN_EXTRA_COLS = (
    ("exit_premium", "REAL"),
    ("exit_spot", "REAL"),
    ("exit_reason", "TEXT"),
    ("lot_size", "INTEGER"),
    ("n_legs", "INTEGER"),
    ("gross_pnl_inr", "REAL"),
    ("brokerage_cost", "REAL"),
    ("slippage_cost", "REAL"),
    ("net_pnl_inr", "REAL"),
    ("closed_at", "TEXT"),
)


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
    existing = {
        r[1] for r in conn.execute("PRAGMA table_info(go_open)").fetchall()
    }
    for col, typ in _GO_OPEN_EXTRA_COLS:
        if col not in existing:
            conn.execute(f"ALTER TABLE go_open ADD COLUMN {col} {typ}")
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


def _lot_and_legs(decision: dict) -> tuple[int, int]:
    ctx = decision.get("context") or {}
    lot = int(ctx.get("lot_size") or 0)
    if lot <= 0:
        try:
            import config
            lot = int(config.get_index(str(decision.get("symbol") or "NIFTY")).lot_size)
        except Exception:
            lot = 65
    n_legs = max(1, len(decision.get("contracts") or []))
    return lot, n_legs


def settle_pnl(
    *,
    entry_premium: float,
    exit_premium: float,
    lot_size: int,
    n_legs: int,
    brokerage_per_order: float = DEFAULT_BROKERAGE_PER_ORDER_INR,
    slippage_pts: float = DEFAULT_SLIPPAGE_PTS_PER_LEG,
) -> dict[str, float]:
    """Gross/net ₹ P&L for a staged GO. Same sign as journal: entry − exit.

    Long debit (entry negative): mark more negative → profit.
    Short credit (entry positive): mark smaller → profit.
    Costs: ₹20 × 2 orders + 0.5 pt × n_legs × lot × round-trip.
    """
    lot = max(1, int(lot_size))
    legs = max(1, int(n_legs))
    gross_pts = float(entry_premium) - float(exit_premium)
    gross_inr = gross_pts * lot
    brokerage = float(brokerage_per_order) * 2.0
    slippage = float(slippage_pts) * legs * lot  # full round-trip
    net_inr = gross_inr - brokerage - slippage
    return {
        "gross_pts": round(gross_pts, 2),
        "gross_pnl_inr": round(gross_inr, 2),
        "brokerage_cost": round(brokerage, 2),
        "slippage_cost": round(slippage, 2),
        "net_pnl_inr": round(net_inr, 2),
        "lot_size": float(lot),
        "n_legs": float(legs),
    }


def unrealized_pnl_inr(
    entry_premium: float | None,
    mark_premium: float | None,
    lot_size: int,
) -> float | None:
    """Mark MTM gross ₹ (no costs until close)."""
    if entry_premium is None or mark_premium is None or lot_size <= 0:
        return None
    return round((float(entry_premium) - float(mark_premium)) * int(lot_size), 2)


def _row_lot_legs(row: sqlite3.Row, decision: dict | None = None) -> tuple[int, int]:
    lot = int(row["lot_size"] or 0) if "lot_size" in row.keys() else 0
    n_legs = int(row["n_legs"] or 0) if "n_legs" in row.keys() else 0
    if lot > 0 and n_legs > 0:
        return lot, n_legs
    dj = decision
    if dj is None:
        try:
            dj = json.loads(row["decision_json"] or "{}")
        except Exception:
            dj = {}
    lot2, legs2 = _lot_and_legs(dj if isinstance(dj, dict) else {})
    return (lot or lot2), (n_legs or legs2)


def _write_settlement(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    entry_premium: float,
    exit_premium: float,
    exit_spot: float | None,
    exit_reason: str,
    status: str,
    lot_size: int,
    n_legs: int,
) -> dict[str, Any]:
    settled = settle_pnl(
        entry_premium=entry_premium,
        exit_premium=exit_premium,
        lot_size=lot_size,
        n_legs=n_legs,
    )
    now = _now_ist()
    conn.execute(
        """
        UPDATE go_open SET
            mark_premium = ?,
            exit_premium = ?,
            exit_spot = ?,
            exit_reason = ?,
            lot_size = ?,
            n_legs = ?,
            gross_pnl_inr = ?,
            brokerage_cost = ?,
            slippage_cost = ?,
            net_pnl_inr = ?,
            status = ?,
            closed_at = ?,
            updated_at = ?
        WHERE decision_id = ?
        """,
        (
            float(exit_premium),
            float(exit_premium),
            exit_spot,
            exit_reason,
            int(settled["lot_size"]),
            int(settled["n_legs"]),
            settled["gross_pnl_inr"],
            settled["brokerage_cost"],
            settled["slippage_cost"],
            settled["net_pnl_inr"],
            status,
            now,
            now,
            decision_id,
        ),
    )
    return {
        "decision_id": decision_id,
        "status": status,
        "exit_premium": float(exit_premium),
        "exit_spot": exit_spot,
        "exit_reason": exit_reason,
        "closed_at": now,
        **settled,
    }


def ensure_schema() -> None:
    with _LOCK:
        c = _conn()
        c.close()


# In-process last EMIT fingerprint per symbol (poll flood killer).
_LAST_EMIT_FP: dict[str, str] = {}


def emit_fingerprint(decision: dict) -> str:
    """Stable board identity — ignores per-poll UUID / tiny score jitter.

    11 Nov minted ~2.3k unique FIRE decision_ids from refresh churn. Stage
    dedupe already used strikes; EMIT must share that discipline.
    """
    legs = decision.get("contracts") or []
    strikes = ",".join(
        f"{c.get('action')}:{c.get('opt_type')}:{c.get('strike')}"
        for c in legs[:8]
    )
    # Bucket score to 5 pts so 81.2 vs 82.9 is not a new emit
    sc = float(decision.get("score") or 0)
    sc_bucket = int(sc // 5) * 5
    return "|".join([
        str(decision.get("symbol") or "").upper(),
        str(decision.get("mode") or ""),
        str(decision.get("state") or ""),
        str(decision.get("strategy_key") or ""),
        strikes,
        str(sc_bucket),
    ])


def record_event(
    event_type: str,
    decision: dict,
    *,
    trade_id: str | None = None,
    force: bool = False,
) -> str | None:
    """Append EMIT / STAGE / SKIP. STAGE also opens a go_open row.

    EMIT is deduped by ``emit_fingerprint`` unless ``force=True``. Returns
    None when an identical board was already ledgered (no new row).
    """
    ensure_schema()
    et = str(event_type or "").upper()
    if et not in ("EMIT", "STAGE", "SKIP"):
        raise ValueError(f"invalid event_type {event_type!r}")

    event_id = f"ge_{uuid.uuid4().hex[:12]}"
    fp = emit_fingerprint(decision) if et == "EMIT" else ""
    sym_key = str(decision.get("symbol") or "").upper() or "_"
    emit_key = f"{sym_key}:{fp}" if et == "EMIT" else ""

    with _LOCK:
        if et == "EMIT" and not force:
            if _LAST_EMIT_FP.get(emit_key) == fp:
                return None
            _LAST_EMIT_FP[emit_key] = fp
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
                lot, n_legs = _lot_and_legs(decision)
                now = _now_ist()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO go_open (
                        decision_id, trade_id, symbol, mode, strategy_key,
                        entry_premium, entry_spot, mark_premium, mark_spot,
                        mfe, mae, trail_armed, trail_stop, status,
                        opened_at, updated_at, decision_json,
                        lot_size, n_legs,
                        exit_premium, exit_spot, exit_reason,
                        gross_pnl_inr, brokerage_cost, slippage_cost,
                        net_pnl_inr, closed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                        lot,
                        n_legs,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
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
            mark_spot_v = mark_spot if mark_spot is not None else row["mark_spot"]
            conn.execute(
                """
                UPDATE go_open SET
                    mark_premium = ?, mark_spot = ?, mfe = ?, mae = ?,
                    trail_armed = ?, trail_stop = ?, status = ?, updated_at = ?
                WHERE decision_id = ?
                """,
                (
                    float(mark_premium),
                    mark_spot_v,
                    round(mfe, 2),
                    round(mae, 2),
                    trail_armed,
                    trail_stop,
                    status,
                    now,
                    decision_id,
                ),
            )
            out: dict[str, Any] = {
                "decision_id": decision_id,
                "mark_premium": float(mark_premium),
                "mfe": round(mfe, 2),
                "mae": round(mae, 2),
                "trail_armed": bool(trail_armed),
                "trail_stop": trail_stop,
                "status": status,
                "trail": trail,
            }
            lot, n_legs = _row_lot_legs(row)
            out["unrealized_pnl_inr"] = unrealized_pnl_inr(
                entry, float(mark_premium), lot
            )
            if status == "TRAIL_STOP" and row["net_pnl_inr"] is None:
                settled = _write_settlement(
                    conn,
                    decision_id=decision_id,
                    entry_premium=entry,
                    exit_premium=float(mark_premium),
                    exit_spot=float(mark_spot_v) if mark_spot_v is not None else None,
                    exit_reason="TRAIL_STOP",
                    status="TRAIL_STOP",
                    lot_size=lot,
                    n_legs=n_legs,
                )
                out.update({
                    "gross_pnl_inr": settled["gross_pnl_inr"],
                    "brokerage_cost": settled["brokerage_cost"],
                    "slippage_cost": settled["slippage_cost"],
                    "net_pnl_inr": settled["net_pnl_inr"],
                    "exit_reason": "TRAIL_STOP",
                    "closed_at": settled["closed_at"],
                })
            conn.commit()
            return out
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


def close_open(
    decision_id: str,
    status: str = "CLOSED",
    *,
    exit_premium: float | None = None,
    exit_spot: float | None = None,
    exit_reason: str = "MANUAL",
) -> dict[str, Any] | None:
    """Close an OPEN staged GO and settle cost-aware ₹ P&L.

    If ``exit_premium`` is omitted, uses the last mark (or entry).
    """
    ensure_schema()
    with _LOCK:
        conn = _conn()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM go_open WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            if row is None:
                return None
            if row["status"] != "OPEN" and row["net_pnl_inr"] is not None:
                return {
                    "decision_id": decision_id,
                    "status": row["status"],
                    "gross_pnl_inr": row["gross_pnl_inr"],
                    "brokerage_cost": row["brokerage_cost"],
                    "slippage_cost": row["slippage_cost"],
                    "net_pnl_inr": row["net_pnl_inr"],
                    "exit_premium": row["exit_premium"],
                    "exit_reason": row["exit_reason"],
                    "closed_at": row["closed_at"],
                    "already_closed": True,
                }
            entry = float(row["entry_premium"] or 0)
            if exit_premium is None:
                exit_premium = float(
                    row["mark_premium"]
                    if row["mark_premium"] is not None
                    else entry
                )
            if exit_spot is None and row["mark_spot"] is not None:
                exit_spot = float(row["mark_spot"])
            lot, n_legs = _row_lot_legs(row)
            settled = _write_settlement(
                conn,
                decision_id=decision_id,
                entry_premium=entry,
                exit_premium=float(exit_premium),
                exit_spot=exit_spot,
                exit_reason=exit_reason or status,
                status=status,
                lot_size=lot,
                n_legs=n_legs,
            )
            conn.commit()
            return settled
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
            lot, n_legs = _row_lot_legs(r)
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
                "lot_size": lot,
                "n_legs": n_legs,
                "unrealized_pnl_inr": unrealized_pnl_inr(
                    r["entry_premium"], r["mark_premium"], lot
                ),
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
                """
                SELECT mfe, mae, trail_armed, status,
                       gross_pnl_inr, net_pnl_inr, brokerage_cost, slippage_cost,
                       mode, strategy_key, closed_at
                FROM go_open WHERE symbol = ?
                """,
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM go_events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            open_rows = conn.execute(
                """
                SELECT mfe, mae, trail_armed, status,
                       gross_pnl_inr, net_pnl_inr, brokerage_cost, slippage_cost,
                       mode, strategy_key, closed_at
                FROM go_open
                """
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

    closed = [
        r for r in open_rows
        if r["status"] in ("CLOSED", "TRAIL_STOP") and r["net_pnl_inr"] is not None
    ]
    nets = [float(r["net_pnl_inr"]) for r in closed]
    grosses = [float(r["gross_pnl_inr"] or 0) for r in closed]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    sum_wins = sum(wins)
    sum_losses_abs = abs(sum(losses))
    pnl = {
        "closed_count": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": sum(1 for n in nets if n == 0),
        "win_rate": round(len(wins) / len(nets), 3) if nets else None,
        "gross_pnl_inr": round(sum(grosses), 2) if closed else None,
        "net_pnl_inr": round(sum(nets), 2) if closed else None,
        "avg_net_pnl_inr": round(sum(nets) / len(nets), 2) if nets else None,
        "profit_factor": (
            round(sum_wins / sum_losses_abs, 2)
            if sum_losses_abs > 0
            else (None if not wins else None)
        ),
        "brokerage_cost_sum": round(
            sum(float(r["brokerage_cost"] or 0) for r in closed), 2
        ) if closed else None,
        "slippage_cost_sum": round(
            sum(float(r["slippage_cost"] or 0) for r in closed), 2
        ) if closed else None,
        "cost_model": {
            "brokerage_per_order_inr": DEFAULT_BROKERAGE_PER_ORDER_INR,
            "slippage_pts_per_leg": DEFAULT_SLIPPAGE_PTS_PER_LEG,
            "note": "₹20×2 orders + 0.5pt×legs×lot round-trip · VERIFY vs broker",
        },
    }
    if sum_wins > 0 and sum_losses_abs == 0:
        pnl["profit_factor"] = None  # undefined when no losses
    elif sum_wins == 0 and sum_losses_abs > 0:
        pnl["profit_factor"] = 0.0

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
        "pnl": pnl,
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
