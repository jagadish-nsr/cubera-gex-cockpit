"""Map-lag / open-thrust runs — price burst while wall-chase criteria fail.

Empirical contrast (prod journals, week ending 2026-11-19): large 30m spot
moves (e.g. Sensex 19 Nov 09:30–10:00 +520) with <2 same-side wall events,
PINNED/between-walls geometry, soft CONTINUATION (<65), and/or conflict tape
(ABSORPTION_BEAR / opposite CONT). Distinct from STRONG wall-chase.

Noise contract (intentional):
  - Informational only — never promotes CALL/PUT WATCH
  - No browser push, no temple bell, no Session Commentary publish
  - Soft alert-tape badge/drivers only while stance stays STAY OUT
  - Tight move thresholds + morning bias + ≥2 fail reasons
"""

from __future__ import annotations

from typing import Any

from analytics.wall_chase import (
    CALL_CHASE_ZONES,
    CHASE_TAPE_MIN_SCORE,
    PUT_CHASE_ZONES,
    evaluate_wall_chase,
    is_momentum_tape_type,
    signal_side_from_alert,
)


# Tighter than the discovery scan (80 / 200 / 250) so small chops stay quiet.
MOVE_THR_PTS = {
    "NIFTY": 120.0,
    "BANKNIFTY": 300.0,
    "SENSEX": 400.0,
}
WINDOW_S = 30 * 60
# After the open window, require a larger burst (cuts afternoon false flags).
MORNING_END_MINS = 10 * 60 + 45  # 10:45 IST from midnight
AFTERNOON_MOVE_MULT = 1.5
SOFT_CONT_MIN = 40  # below CHASE_TAPE_MIN_SCORE


def _f(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def move_threshold(symbol: str, *, mins_of_day: int | None = None) -> float:
    sym = str(symbol or "NIFTY").upper()
    base = float(MOVE_THR_PTS.get(sym, MOVE_THR_PTS["NIFTY"]))
    if mins_of_day is None:
        return base
    if mins_of_day <= MORNING_END_MINS:
        return base
    return round(base * AFTERNOON_MOVE_MULT, 1)


def _mins_of_day(now_ts: float) -> int:
    try:
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        dt = datetime.fromtimestamp(float(now_ts), tz=ist)
        return dt.hour * 60 + dt.minute
    except Exception:
        return 12 * 60


def measure_spot_run(
    spot_path: list | None,
    *,
    now_ts: float,
    window_s: float = WINDOW_S,
) -> dict:
    """Largest signed spot change ending near now_ts inside window_s."""
    empty = {
        "move_pts": 0.0,
        "abs_move_pts": 0.0,
        "side": "",
        "spot0": 0.0,
        "spot1": 0.0,
        "t0": None,
        "t1": None,
        "n_samples": 0,
    }
    pts: list[tuple[float, float]] = []
    for row in spot_path or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            ts, spot = _f(row[0]), _f(row[1])
        elif isinstance(row, dict):
            ts = _f(row.get("ts") or row.get("epoch"))
            spot = _f(row.get("spot") or row.get("price"))
        else:
            continue
        if ts <= 0 or spot <= 0:
            continue
        if now_ts - ts < 0 or now_ts - ts > float(window_s) + 5:
            continue
        pts.append((ts, spot))
    if len(pts) < 2:
        return empty
    pts.sort(key=lambda x: x[0])
    spot1 = pts[-1][1]
    t1 = pts[-1][0]
    # Prefer the earliest sample in window as start (open-thrust shape).
    spot0 = pts[0][1]
    t0 = pts[0][0]
    # Also check max adverse excursion start for a stronger signed run
    best = spot1 - spot0
    best_t0, best_s0 = t0, spot0
    for ts, spot in pts[:-1]:
        move = spot1 - spot
        if abs(move) > abs(best):
            best = move
            best_t0, best_s0 = ts, spot
    side = "CALL" if best > 0 else ("PUT" if best < 0 else "")
    return {
        "move_pts": round(best, 1),
        "abs_move_pts": round(abs(best), 1),
        "side": side,
        "spot0": best_s0,
        "spot1": spot1,
        "t0": best_t0,
        "t1": t1,
        "n_samples": len(pts),
    }


def _fail_reasons(
    *,
    side: str,
    zone: str,
    wall_chase: dict,
    alerts: list | None,
    now_ts: float,
) -> list[str]:
    reasons: list[str] = []
    chase = wall_chase or {}
    chase_side = str(chase.get("side") or "").upper() if chase.get("active") else ""
    n_ev = int(chase.get("n_events") or 0)

    if chase.get("active") and chase_side and chase_side != side:
        reasons.append("OPP_SIDE_WALLS")
    elif n_ev < 2 or not chase.get("active") or chase_side != side:
        reasons.append("LT2_SAME_SIDE_WALLS")

    z = str(zone or "").upper()
    chase_zones = CALL_CHASE_ZONES if side == "CALL" else (
        PUT_CHASE_ZONES if side == "PUT" else frozenset()
    )
    if z not in chase_zones:
        reasons.append(f"ZONE_{z or 'UNKNOWN'}")

    recent = []
    for a in alerts or []:
        try:
            score = int(a.get("score") or 0)
        except (TypeError, ValueError):
            continue
        ts = a.get("ts") or a.get("_ts") or a.get("epoch")
        try:
            ts_f = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts_f = None
        if ts_f is None:
            continue
        age = now_ts - ts_f
        if age < 0 or age > 45 * 60:
            continue
        recent.append({**a, "score": score, "_side": signal_side_from_alert(a)})

    agree = [
        a for a in recent
        if is_momentum_tape_type(a.get("type"))
        and a["score"] >= CHASE_TAPE_MIN_SCORE
        and a.get("_side") == side
    ]
    soft = [
        a for a in recent
        if is_momentum_tape_type(a.get("type"))
        and SOFT_CONT_MIN <= a["score"] < CHASE_TAPE_MIN_SCORE
        and a.get("_side") == side
    ]
    conflict = [
        a for a in recent
        if a["score"] >= 65
        and a.get("_side")
        and a.get("_side") != side
    ]

    if not agree:
        if soft:
            reasons.append("SOFT_CONT_<65")
        else:
            reasons.append("NO_MOM_TAPE")
    if conflict:
        # Conflict only counts when it rivals or beats agreeing tape
        conf_best = max(c["score"] for c in conflict)
        agree_best = max((a["score"] for a in agree), default=0)
        if conf_best >= agree_best:
            reasons.append("CONFLICT_TAPE")

    return reasons


def evaluate_map_lag_run(
    *,
    symbol: str,
    spot_path: list | None,
    zone: str,
    wall_events: list | None,
    orderflow_alerts: list | None,
    now_ts: float,
    wall_chase: dict | None = None,
) -> dict:
    """Return quiet map-lag read. ``active`` never implies a WATCH stance."""
    empty = {
        "active": False,
        "side": "",
        "move_pts": 0.0,
        "abs_move_pts": 0.0,
        "threshold_pts": 0.0,
        "reasons": [],
        "reason_n": 0,
        "morning": False,
        "note": "",
    }
    run = measure_spot_run(spot_path, now_ts=now_ts)
    mins = _mins_of_day(now_ts)
    thr = move_threshold(symbol, mins_of_day=mins)
    morning = mins <= MORNING_END_MINS
    if run["abs_move_pts"] < thr or not run["side"]:
        out = dict(empty)
        out["threshold_pts"] = thr
        out["abs_move_pts"] = run["abs_move_pts"]
        out["move_pts"] = run["move_pts"]
        out["morning"] = morning
        return out

    chase = wall_chase if wall_chase is not None else evaluate_wall_chase(
        wall_events, now_ts=now_ts,
    )
    side = run["side"]
    z = str(zone or "").upper()
    # Suppress when a true same-side wall-chase + chase zone is already on
    agree_mom = False
    for a in orderflow_alerts or []:
        try:
            score = int(a.get("score") or 0)
        except (TypeError, ValueError):
            continue
        if (
            is_momentum_tape_type(a.get("type"))
            and score >= CHASE_TAPE_MIN_SCORE
            and signal_side_from_alert(a) == side
        ):
            ts = a.get("ts") or a.get("_ts")
            try:
                ts_f = float(ts) if ts is not None else None
            except (TypeError, ValueError):
                ts_f = None
            if ts_f is not None and 0 <= now_ts - ts_f <= 45 * 60:
                agree_mom = True
                break
    chase_zones = CALL_CHASE_ZONES if side == "CALL" else PUT_CHASE_ZONES
    if (
        chase.get("active")
        and str(chase.get("side") or "").upper() == side
        and z in chase_zones
        and agree_mom
    ):
        out = dict(empty)
        out["threshold_pts"] = thr
        out["move_pts"] = run["move_pts"]
        out["abs_move_pts"] = run["abs_move_pts"]
        out["side"] = side
        out["morning"] = morning
        out["note"] = "Suppressed — same-side wall-chase already qualifies."
        return out

    reasons = _fail_reasons(
        side=side,
        zone=z,
        wall_chase=chase,
        alerts=orderflow_alerts,
        now_ts=now_ts,
    )
    # Require ≥2 independent fail reasons (noise gate from week sample).
    active = len(reasons) >= 2
    return {
        "active": active,
        "side": side if active else "",
        "move_pts": run["move_pts"],
        "abs_move_pts": run["abs_move_pts"],
        "threshold_pts": thr,
        "spot0": run["spot0"],
        "spot1": run["spot1"],
        "reasons": reasons if active else [],
        "reason_n": len(reasons) if active else 0,
        "morning": morning,
        "window_s": float(WINDOW_S),
        "note": (
            "Map-lag / open thrust: price ran while walls/tape lagged. "
            "Informational only — not CALL/PUT WATCH; not a dealer hedge claim."
            if active else ""
        ),
    }
