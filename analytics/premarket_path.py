"""Premarket regime path ledger — discovery seal → checkpoints → settle efficiency.

No VWAP invention. No auto-execution. pin_efficiency_pct is settle location only;
path_efficiency_pct is thesis + excursion + gap (see score_path).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import config

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

CHECKPOINTS = (
    "DISCOVERY",
    "T0915",
    "T0920",
    "T0930",
    "T1000",
    "T1100",
    "T1400",
    "SETTLE",
    "ADHOC",
)

# IST minute-of-day → checkpoint id (sampled once when clock crosses).
_SESSION_CHECKPOINT_MINS = (
    (9 * 60 + 15, "T0915"),
    (9 * 60 + 20, "T0920"),
    (9 * 60 + 30, "T0930"),
    (10 * 60, "T1000"),
    (11 * 60, "T1100"),
    (14 * 60, "T1400"),
)

_CE_UP = frozenset({"CALL_WALL_BREACH_SQUEEZE", "GAP_UP_RUNAWAY", "SHORT_GAMMA_WALL_TEST"})
_PE_DOWN = frozenset({
    "PUT_WALL_BREAKDOWN_CASCADE", "GAP_DOWN_TRAP", "PUT_WALL_BREAKDOWN_TEST",
})
_MAP = frozenset({
    "GAP_UP_INTO_POS_GAMMA", "GAP_DOWN_INTO_POS_GAMMA",
    "AUCTION_BALANCED", "MODERATE_DRIFT",
})
_NO_FADE = frozenset({"GAP_UP_INTO_POS_GAMMA", "GAP_UP_RUNAWAY"})
_NO_BOUNCE = frozenset({"GAP_DOWN_INTO_POS_GAMMA", "GAP_DOWN_TRAP"})

_PATH_THREADS: dict[str, threading.Thread] = {}
_PATH_LOCK = threading.Lock()


def _db():
    from data.pre_post_market import _get_db_conn, _DB_LOCK, init_pre_post_db
    init_pre_post_db()
    return _get_db_conn, _DB_LOCK


def ensure_path_schema() -> None:
    """Ensure path events table + auction path columns (idempotent)."""
    get_conn, db_lock = _db()
    with db_lock, get_conn() as conn:
        # Tables are created in init_pre_post_db; ALTER covers older DBs.
        for col, col_type in (
            ("path_json", "TEXT"),
            ("thesis_outcome", "TEXT"),
            ("path_mfe_pts", "REAL"),
            ("path_mae_pts", "REAL"),
            ("path_efficiency_pct", "REAL"),
            ("path_scored_at", "TEXT"),
            ("path_score_detail_json", "TEXT"),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE market_auctions_history ADD COLUMN {col} {col_type}"
                )
            except Exception:
                pass
        conn.commit()


def thesis_state_at(
    regime: str,
    *,
    open_px: float,
    spot: float,
    cw: float,
    pw: float,
    flip: float,
    prev: float,
    gap_fill: str,
    step: float = 50.0,
) -> str:
    """Locate spot vs the sealed discovery thesis. Hand-checkable."""
    r = (regime or "").upper()
    fill = str(gap_fill or "UNFILLED").upper() == "FILLED"
    if spot <= 0 or open_px <= 0:
        return "PENDING"

    if r == "CALL_WALL_BREACH_SQUEEZE":
        if cw <= 0:
            return "PENDING"
        return "HOLDING" if spot >= cw else "INVALIDATED"
    if r == "PUT_WALL_BREAKDOWN_CASCADE":
        if pw <= 0:
            return "PENDING"
        return "HOLDING" if spot <= pw else "INVALIDATED"
    if r == "SHORT_GAMMA_WALL_TEST":
        if cw <= 0:
            return "PENDING"
        if spot >= cw + step:
            return "HOLDING"
        if spot <= cw - step:
            return "INVALIDATED"
        return "PENDING"
    if r == "PUT_WALL_BREAKDOWN_TEST":
        if pw <= 0:
            return "PENDING"
        if spot <= pw - step:
            return "HOLDING"
        if spot >= pw + step:
            return "INVALIDATED"
        return "PENDING"
    if r == "GAP_UP_INTO_POS_GAMMA":
        # NO FADE: breach if gap fills and price extends through prior against open.
        if fill and prev > 0 and spot >= prev:
            return "MAP_BREACH"
        return "MAP_OK"
    if r == "GAP_DOWN_INTO_POS_GAMMA":
        if fill and prev > 0 and spot <= prev:
            return "MAP_BREACH"
        return "MAP_OK"
    if r == "GAP_UP_RUNAWAY":
        if fill and prev > 0 and spot < open_px and spot <= prev:
            return "INVALIDATED"
        return "HOLDING"
    if r == "GAP_DOWN_TRAP":
        if fill and prev > 0 and spot > open_px and spot >= prev:
            return "INVALIDATED"
        return "HOLDING"
    if r in ("AUCTION_BALANCED", "MODERATE_DRIFT"):
        return "MAP_OK"
    return "PENDING"


def _auction_row(symbol: str, trade_date: str) -> dict | None:
    get_conn, db_lock = _db()
    with db_lock, get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM market_auctions_history WHERE trade_date = ? AND symbol = ?",
            (trade_date, symbol.upper()),
        ).fetchone()
        return dict(row) if row else None


def _next_seq(conn, trade_date: str, symbol: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS m FROM premarket_path_events "
        "WHERE trade_date = ? AND symbol = ?",
        (trade_date, symbol),
    ).fetchone()
    return int(row["m"] if row else 0) + 1


def _checkpoint_exists(conn, trade_date: str, symbol: str, checkpoint: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM premarket_path_events "
        "WHERE trade_date = ? AND symbol = ? AND checkpoint = ?",
        (trade_date, symbol, checkpoint),
    ).fetchone()
    return row is not None


def _spot_now(symbol: str) -> float:
    try:
        from app.spot_ticker import get_instant_quote
        q = get_instant_quote(symbol) or {}
        spot = float(q.get("spot") or 0)
        if spot > 0:
            return spot
    except Exception:
        pass
    try:
        from data.market_snapshot import get_snapshot
        snap = get_snapshot(symbol) or {}
        return float(snap.get("spot") or 0)
    except Exception:
        return 0.0


def _walls_from_row(row: dict) -> tuple[float, float, float, float, float]:
    """open, cw, pw, flip, prev — prefer sealed map from opening_watch_json."""
    open_px = float(row.get("discovered_spot") or 0)
    prev = float(row.get("prev_close") or 0)
    cw = pw = flip = 0.0
    raw = row.get("opening_watch_json")
    if raw:
        try:
            w = json.loads(raw) if isinstance(raw, str) else raw
            ms = (w or {}).get("map_strip") or {}
            open_px = float(ms.get("open") or open_px)
            cw = float(ms.get("call_wall") or 0)
            pw = float(ms.get("put_wall") or 0)
            flip = float(ms.get("gamma_flip") or 0)
            prev = float(ms.get("prev_close") or prev)
        except Exception:
            pass
    return open_px, cw, pw, flip, prev


def _inside_5m(spot: float, watch_json: str | None) -> bool | None:
    if not watch_json or spot <= 0:
        return None
    try:
        w = json.loads(watch_json) if isinstance(watch_json, str) else watch_json
        est = (w or {}).get("first_5m_estimate") or {}
        hi = est.get("high")
        lo = est.get("low")
        if hi is None or lo is None:
            return None
        return float(lo) <= spot <= float(hi)
    except Exception:
        return None


def append_path_event(
    symbol: str,
    checkpoint: str,
    spot: float,
    *,
    trade_date: str | None = None,
    gap_fill: str | None = None,
    note: str = "",
    force: bool = False,
) -> dict | None:
    """Idempotent append for a named checkpoint (except ADHOC)."""
    ensure_path_schema()
    sym = config.index_key(symbol)
    t_date = trade_date or datetime.now(IST).strftime("%Y-%m-%d")
    ck = str(checkpoint or "").upper()
    if ck not in CHECKPOINTS:
        raise ValueError(f"unknown checkpoint {checkpoint}")
    if spot <= 0:
        return None

    get_conn, db_lock = _db()
    with db_lock, get_conn() as conn:
        if ck != "ADHOC" and not force and _checkpoint_exists(conn, t_date, sym, ck):
            return None
        row = conn.execute(
            "SELECT * FROM market_auctions_history WHERE trade_date = ? AND symbol = ?",
            (t_date, sym),
        ).fetchone()
        if row is None:
            log.debug("path event skipped — no auction row %s %s", t_date, sym)
            return None
        auction = dict(row)
        open_px, cw, pw, flip, prev = _walls_from_row(auction)
        regime = str(auction.get("gap_regime") or "")
        gfill = gap_fill or auction.get("gap_fill_status") or "UNFILLED"
        step = float(config.strike_step_for(sym))
        thesis = thesis_state_at(
            regime,
            open_px=open_px,
            spot=float(spot),
            cw=cw,
            pw=pw,
            flip=flip,
            prev=prev,
            gap_fill=str(gfill),
            step=step,
        )
        inside = _inside_5m(float(spot), auction.get("opening_watch_json"))
        now_ist = datetime.now(IST)
        seq = _next_seq(conn, t_date, sym)
        event = {
            "trade_date": t_date,
            "symbol": sym,
            "seq": seq,
            "ts_ist": now_ist.isoformat(timespec="seconds"),
            "checkpoint": ck,
            "spot": float(spot),
            "spot_vs_open_pts": round(float(spot) - open_px, 2) if open_px else None,
            "spot_vs_flip_pts": round(float(spot) - flip, 2) if flip else None,
            "spot_vs_cw_pts": round(float(spot) - cw, 2) if cw else None,
            "spot_vs_pw_pts": round(float(spot) - pw, 2) if pw else None,
            "gap_fill": str(gfill).upper(),
            "inside_5m_envelope": None if inside is None else (1 if inside else 0),
            "thesis_state": thesis,
            "note": note or "",
        }
        conn.execute(
            """
            INSERT INTO premarket_path_events (
                trade_date, symbol, seq, ts_ist, checkpoint, spot,
                spot_vs_open_pts, spot_vs_flip_pts, spot_vs_cw_pts, spot_vs_pw_pts,
                gap_fill, inside_5m_envelope, thesis_state, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["trade_date"], event["symbol"], event["seq"], event["ts_ist"],
                event["checkpoint"], event["spot"],
                event["spot_vs_open_pts"], event["spot_vs_flip_pts"],
                event["spot_vs_cw_pts"], event["spot_vs_pw_pts"],
                event["gap_fill"], event["inside_5m_envelope"],
                event["thesis_state"], event["note"],
            ),
        )
        conn.commit()
        return event


def seal_discovery(symbol: str, state: dict) -> dict | None:
    """Write DISCOVERY checkpoint from a just-discovered pre-market state."""
    if not state or not state.get("is_discovered"):
        return None
    spot = float(state.get("discovered_spot") or 0)
    if spot <= 0:
        return None
    ensure_path_schema()
    return append_path_event(
        symbol,
        "DISCOVERY",
        spot,
        trade_date=state.get("date"),
        gap_fill=state.get("gap_fill_status") or "UNFILLED",
        note=f"regime={state.get('gap_regime') or ''}",
    )


def reseal_discovery(symbol: str, state: dict) -> dict | None:
    """Rewrite DISCOVERY after an LTP → official-open upgrade.

    First seal is idempotent; when Dhan later publishes day-open the auction
    row (spot, gap_regime) updates but the DISCOVERY path row used to stay
    frozen on the provisional LTP print/regime. That made today's BN/SX notes
    disagree with the auction gap_regime. Only rewrites DISCOVERY; later
    checkpoints keep their spots (score_path uses auction discovered_spot).
    """
    if not state or not state.get("is_discovered"):
        return None
    spot = float(state.get("discovered_spot") or 0)
    if spot <= 0:
        return None
    ensure_path_schema()
    sym = config.index_key(symbol)
    t_date = state.get("date") or datetime.now(IST).strftime("%Y-%m-%d")
    note = f"regime={state.get('gap_regime') or ''}"
    gfill = str(state.get("gap_fill_status") or "UNFILLED").upper()

    get_conn, db_lock = _db()
    with db_lock, get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM market_auctions_history WHERE trade_date = ? AND symbol = ?",
            (t_date, sym),
        ).fetchone()
        if row is None:
            return None
        auction = dict(row)
        open_px, cw, pw, flip, prev = _walls_from_row(auction)
        # Prefer the just-upgraded state over a stale auction / map_strip race.
        open_px = spot if spot > 0 else open_px
        cw_s = float(state.get("call_wall") or 0)
        pw_s = float(state.get("put_wall") or 0)
        flip_s = float(state.get("gamma_flip") or 0)
        if cw_s > 0:
            cw = cw_s
        if pw_s > 0:
            pw = pw_s
        if flip_s > 0:
            flip = flip_s
        regime = str(state.get("gap_regime") or auction.get("gap_regime") or "")
        step = float(config.strike_step_for(sym))
        thesis = thesis_state_at(
            regime,
            open_px=open_px,
            spot=spot,
            cw=cw,
            pw=pw,
            flip=flip,
            prev=float(state.get("prev_close") or prev or 0),
            gap_fill=gfill,
            step=step,
        )
        existing = conn.execute(
            "SELECT seq, spot, note, thesis_state, spot_vs_cw_pts, spot_vs_pw_pts "
            "FROM premarket_path_events "
            "WHERE trade_date = ? AND symbol = ? AND checkpoint = 'DISCOVERY'",
            (t_date, sym),
        ).fetchone()
        if existing is None:
            return None
        old = dict(existing)
        new_vs_open = 0.0  # DISCOVERY is the open print after reseal
        new_vs_flip = round(spot - flip, 2) if flip else None
        new_vs_cw = round(spot - cw, 2) if cw else None
        new_vs_pw = round(spot - pw, 2) if pw else None
        # Also refresh when sealed walls moved (23-Nov BN DISCOVERY kept CW=64500).
        def _dist_changed(old_v, new_v) -> bool:
            if new_v is None and old_v is None:
                return False
            if new_v is None or old_v is None:
                return True
            return abs(float(old_v) - float(new_v)) >= 0.05

        unchanged = (
            abs(float(old.get("spot") or 0) - spot) < 0.05
            and str(old.get("note") or "") == note
            and str(old.get("thesis_state") or "") == thesis
            and not _dist_changed(old.get("spot_vs_cw_pts"), new_vs_cw)
            and not _dist_changed(old.get("spot_vs_pw_pts"), new_vs_pw)
        )
        if unchanged:
            return None
        now_ist = datetime.now(IST).isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE premarket_path_events
            SET ts_ist = ?, spot = ?,
                spot_vs_open_pts = ?, spot_vs_flip_pts = ?,
                spot_vs_cw_pts = ?, spot_vs_pw_pts = ?,
                gap_fill = ?, thesis_state = ?, note = ?
            WHERE trade_date = ? AND symbol = ? AND checkpoint = 'DISCOVERY'
            """,
            (
                now_ist, spot,
                new_vs_open, new_vs_flip, new_vs_cw, new_vs_pw,
                gfill, thesis, note,
                t_date, sym,
            ),
        )
        conn.commit()
        log.info(
            "path DISCOVERY resealed %s %s spot %.2f→%.2f note %r→%r",
            sym, t_date, float(old.get("spot") or 0), spot,
            old.get("note"), note,
        )
        return {
            "trade_date": t_date,
            "symbol": sym,
            "seq": old.get("seq"),
            "ts_ist": now_ist,
            "checkpoint": "DISCOVERY",
            "spot": spot,
            "spot_vs_open_pts": new_vs_open,
            "spot_vs_flip_pts": new_vs_flip,
            "spot_vs_cw_pts": new_vs_cw,
            "spot_vs_pw_pts": new_vs_pw,
            "gap_fill": gfill,
            "thesis_state": thesis,
            "note": note,
            "resealed": True,
        }


def list_path_events(symbol: str, trade_date: str | None = None) -> list[dict]:
    ensure_path_schema()
    sym = config.index_key(symbol)
    t_date = trade_date or datetime.now(IST).strftime("%Y-%m-%d")
    get_conn, db_lock = _db()
    with db_lock, get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM premarket_path_events WHERE trade_date = ? AND symbol = ? "
            "ORDER BY seq ASC",
            (t_date, sym),
        ).fetchall()
        return [dict(r) for r in rows]


def _thesis_component(state: str) -> float:
    s = (state or "").upper()
    if s in ("HOLDING", "HOLD", "MAP_OK"):
        return 100.0
    if s in ("INVALIDATED", "FAIL", "MAP_BREACH"):
        return 0.0
    return 50.0  # PENDING / unknown at settle


def _excursion_component(regime: str, mfe: float, mae: float, open_px: float) -> float:
    r = (regime or "").upper()
    # mae stored as most adverse (negative if below open when we track signed from open)
    adverse = abs(min(0.0, mae)) if mae is not None else 0.0
    favorable = max(0.0, mfe) if mfe is not None else 0.0
    scale = max(open_px * 0.002, 20.0)  # ~0.2% of spot or 20 pts floor
    if r in _CE_UP:
        # Want upside MFE; penalize adverse MAE
        score = 100.0 * favorable / (favorable + adverse + 1e-9)
        return max(0.0, min(100.0, score))
    if r in _PE_DOWN:
        # Favorable is downside — mfe_down = -mae when mae is min excursion
        down = abs(min(0.0, mae)) if mae is not None else 0.0
        up = max(0.0, mfe) if mfe is not None else 0.0
        score = 100.0 * down / (down + up + 1e-9)
        return max(0.0, min(100.0, score))
    # MAP: high score if adverse move stayed small vs scale
    if adverse <= scale:
        return 100.0
    return max(0.0, 100.0 * (1.0 - (adverse - scale) / (3.0 * scale)))


def _gap_component(regime: str, gap_fill: str) -> float:
    r = (regime or "").upper()
    filled = str(gap_fill or "").upper() == "FILLED"
    if r in _NO_FADE | _NO_BOUNCE | frozenset({"GAP_UP_RUNAWAY", "GAP_DOWN_TRAP"}):
        # Hold of gap thesis prefers UNFILLED for runaway/trap; NO_FADE/NO_BOUNCE
        # prefer gap not filling against the open.
        return 0.0 if filled else 100.0
    if r in _MAP:
        return 100.0  # gap fill is not a map failure
    # Wall hold regimes: fill is informational
    return 50.0 if filled else 75.0


def score_path(symbol: str, trade_date: str | None = None) -> dict | None:
    """Settle scorer — writes path_* onto market_auctions_history."""
    ensure_path_schema()
    sym = config.index_key(symbol)
    t_date = trade_date or datetime.now(IST).strftime("%Y-%m-%d")
    auction = _auction_row(sym, t_date)
    if not auction or not auction.get("is_discovered"):
        log.warning("score_path: discovery missing for %s %s", sym, t_date)
        return None

    events = list_path_events(sym, t_date)
    open_px, cw, pw, flip, prev = _walls_from_row(auction)
    regime = str(auction.get("gap_regime") or "")
    settle_spot = float(
        auction.get("settlement_spot")
        or auction.get("last_continuous_spot")
        or 0
    )
    if settle_spot <= 0 and events:
        settle_spot = float(events[-1].get("spot") or 0)

    # Append SETTLE if missing and we have a print
    if settle_spot > 0:
        append_path_event(
            sym, "SETTLE", settle_spot,
            trade_date=t_date,
            gap_fill=auction.get("gap_fill_status"),
            note="settle",
        )
        events = list_path_events(sym, t_date)

    spots = [float(e["spot"]) for e in events if e.get("spot")]
    if open_px > 0 and spots:
        signed = [s - open_px for s in spots]
        mfe = max(signed)
        mae = min(signed)
    else:
        mfe = mae = 0.0

    last_thesis = "PENDING"
    for e in reversed(events):
        if e.get("thesis_state"):
            last_thesis = str(e["thesis_state"])
            break
    # Final outcome enum
    if last_thesis in ("HOLDING",):
        outcome = "HOLD"
    elif last_thesis in ("INVALIDATED",):
        outcome = "FAIL"
    elif last_thesis in ("MAP_OK",):
        outcome = "MAP_OK"
    elif last_thesis in ("MAP_BREACH",):
        outcome = "MAP_BREACH"
    else:
        outcome = "INCOMPLETE"

    t_comp = _thesis_component(last_thesis)
    e_comp = _excursion_component(regime, mfe, mae, open_px)
    g_comp = _gap_component(regime, str(auction.get("gap_fill_status") or ""))
    eff = round(0.60 * t_comp + 0.25 * e_comp + 0.15 * g_comp, 1)
    detail = {
        "thesis_state": last_thesis,
        "thesis_component": t_comp,
        "excursion_component": round(e_comp, 1),
        "gap_component": g_comp,
        "mfe_pts": round(mfe, 2),
        "mae_pts": round(mae, 2),
        "n_events": len(events),
    }
    path_json = json.dumps(
        [{"checkpoint": e.get("checkpoint"), "spot": e.get("spot"),
          "thesis_state": e.get("thesis_state"), "ts_ist": e.get("ts_ist")}
         for e in events]
    )
    scored_at = datetime.now(IST).isoformat(timespec="seconds")

    get_conn, db_lock = _db()
    with db_lock, get_conn() as conn:
        conn.execute(
            """
            UPDATE market_auctions_history
            SET path_json = ?,
                thesis_outcome = ?,
                path_mfe_pts = ?,
                path_mae_pts = ?,
                path_efficiency_pct = ?,
                path_scored_at = ?,
                path_score_detail_json = ?,
                updated_at = ?
            WHERE trade_date = ? AND symbol = ?
            """,
            (
                path_json, outcome, round(mfe, 2), round(mae, 2), eff,
                scored_at, json.dumps(detail),
                datetime.now(timezone.utc).isoformat(),
                t_date, sym,
            ),
        )
        conn.commit()

    out = {
        "symbol": sym,
        "trade_date": t_date,
        "thesis_outcome": outcome,
        "path_mfe_pts": round(mfe, 2),
        "path_mae_pts": round(mae, 2),
        "path_efficiency_pct": eff,
        "path_scored_at": scored_at,
        "detail": detail,
        "events": events,
    }
    log.info(
        "path scored %s %s outcome=%s eff=%.1f mfe=%.1f mae=%.1f",
        sym, t_date, outcome, eff, mfe, mae,
    )
    return out


def regime_path_stats(
    symbol: str,
    regime: str,
    *,
    lookback_sessions: int = 40,
) -> dict[str, Any]:
    """Historical base rates for the same gap_regime on this index."""
    ensure_path_schema()
    sym = config.index_key(symbol)
    reg = str(regime or "").upper()
    get_conn, db_lock = _db()
    with db_lock, get_conn() as conn:
        rows = conn.execute(
            """
            SELECT thesis_outcome, path_efficiency_pct, path_mfe_pts, path_mae_pts,
                   gap_fill_status, trade_date
            FROM market_auctions_history
            WHERE symbol = ?
              AND UPPER(COALESCE(gap_regime, '')) = ?
              AND path_scored_at IS NOT NULL
              AND path_efficiency_pct IS NOT NULL
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (sym, reg, int(lookback_sessions)),
        ).fetchall()
    n = len(rows)
    if n == 0:
        return {
            "symbol": sym,
            "regime": reg,
            "n": 0,
            "hold_rate": None,
            "mean_eff": None,
            "median_mfe": None,
            "median_mae": None,
            "gap_fill_rate": None,
            "confidence": "LOW",
            "note": "No scored paths for this regime yet.",
        }

    def _hold(o: str | None) -> bool:
        return str(o or "").upper() in ("HOLD", "MAP_OK")

    holds = sum(1 for r in rows if _hold(r["thesis_outcome"]))
    effs = [float(r["path_efficiency_pct"]) for r in rows if r["path_efficiency_pct"] is not None]
    mfes = [float(r["path_mfe_pts"]) for r in rows if r["path_mfe_pts"] is not None]
    maes = [float(r["path_mae_pts"]) for r in rows if r["path_mae_pts"] is not None]
    fills = sum(1 for r in rows if str(r["gap_fill_status"] or "").upper() == "FILLED")

    def _med(xs: list[float]) -> float | None:
        if not xs:
            return None
        s = sorted(xs)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0

    hold_rate = holds / n
    mean_eff = sum(effs) / len(effs) if effs else None
    confidence = "LOW" if n < 8 else ("OK" if hold_rate >= 0.55 and (mean_eff or 0) >= 55 else "WATCH")
    return {
        "symbol": sym,
        "regime": reg,
        "n": n,
        "hold_rate": round(hold_rate, 3),
        "mean_eff": round(mean_eff, 1) if mean_eff is not None else None,
        "median_mfe": round(_med(mfes), 1) if _med(mfes) is not None else None,
        "median_mae": round(_med(maes), 1) if _med(maes) is not None else None,
        "gap_fill_rate": round(fills / n, 3),
        "confidence": confidence,
        "note": f"Last {n} scored {reg} session(s) on {sym}.",
    }


def sample_due_checkpoints(symbol: str, now: datetime | None = None) -> list[str]:
    """Fire any session checkpoints whose IST minute has arrived (idempotent).

    Requires today's DISCOVERY seal first. Sampling T0915 before discovery
    (23-Nov Nifty/Sensex) wrote stale prior-book walls and bogus thesis states.
    """
    now_ist = now or datetime.now(IST)
    from data.session_calendar import is_live_session_day
    if not is_live_session_day(now_ist.date()):
        return []
    mins = now_ist.hour * 60 + now_ist.minute
    if mins < 9 * 60 + 15 or mins >= 15 * 60 + 30:
        return []
    spot = _spot_now(symbol)
    if spot <= 0:
        return []
    fired: list[str] = []
    t_date = now_ist.strftime("%Y-%m-%d")
    sym = config.index_key(symbol)
    get_conn, db_lock = _db()
    with db_lock, get_conn() as conn:
        if not _checkpoint_exists(conn, t_date, sym, "DISCOVERY"):
            return []
    for thresh, ck in _SESSION_CHECKPOINT_MINS:
        if mins >= thresh:
            ev = append_path_event(symbol, ck, spot, trade_date=t_date)
            if ev:
                fired.append(ck)
    return fired


def _path_loop(symbol: str) -> None:
    while True:
        now = datetime.now(IST)
        try:
            from data.session_calendar import is_live_session_day
            mins = now.hour * 60 + now.minute
            awake = (
                is_live_session_day(now.date())
                and (9 * 60 + 15) <= mins < (15 * 60 + 30)
            )
            if awake:
                sample_due_checkpoints(symbol, now)
                time.sleep(20.0)
            else:
                time.sleep(60.0)
        except Exception as exc:
            log.debug("path poller %s: %s", symbol, exc)
            time.sleep(30.0)


def ensure_path_poller(symbol: str = "NIFTY") -> None:
    """Ingest-side daemon: sample path checkpoints through the cash session."""
    sym = symbol.upper().strip()
    with _PATH_LOCK:
        t = _PATH_THREADS.get(sym)
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_path_loop, args=(sym,), daemon=True, name=f"pm-path-{sym}")
        _PATH_THREADS[sym] = t
        t.start()


def ensure_path_pollers() -> None:
    ensure_path_schema()
    for s in ("NIFTY", "BANKNIFTY", "SENSEX"):
        ensure_path_poller(s)
