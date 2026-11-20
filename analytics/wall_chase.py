"""Wall-chase / successive-break momentum criteria.

Empirical sample (prod journals, 2026-11-06 → 2026-11-19):
  - 20 STRONG episodes = ≥2 same-side WALL_BREAK/MIGRATE in 45m
    + futures move in wall direction with CVD agree
    + CONTINUATION_/CVD_DIVERGENCE_ tape on the same side
  - 6/20 STRONG peaked at tape scores 65–69 (incl. Sensex 19 Nov 13:40–14:25)
    and were blocked by the global SIGNAL_MIN_SCORE=70 gate
  - ABOVE_CALL_WALL / ABOVE_MAGNET stayed STAY OUT even with bullish tape

This module is the shared criteria surface for market_alert (and tests).
It does not invent a dealer hedge. Walls are concentration maps; tape is
order-flow; the composite is a watch, not auto-execution.
"""

from __future__ import annotations

from typing import Any


# Soft floor for momentum tape types when the GEX zone is chase-friendly.
# Hard floor (70) still applies to absorption / exhaustion / other prints.
CHASE_TAPE_MIN_SCORE = 65
WALL_CHASE_WINDOW_S = 45 * 60

MOMENTUM_TAPE_TYPES = frozenset({
    "CONTINUATION_BULL",
    "CONTINUATION_BEAR",
    "CVD_DIVERGENCE_BULL",
    "CVD_DIVERGENCE_BEAR",
})

CALL_CHASE_ZONES = frozenset({
    "ABOVE_CALL_WALL",
    "ABOVE_MAGNET",
    "TEST_CALL",
})
PUT_CHASE_ZONES = frozenset({
    "BELOW_PUT_WALL",
    "BELOW_MAGNET",
    "TEST_PUT",
})


def _f(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def is_momentum_tape_type(sig_type: str | None) -> bool:
    return str(sig_type or "").upper() in MOMENTUM_TAPE_TYPES


def chase_side_for_zone(zone: str | None) -> str:
    """Return CALL, PUT, or '' when the zone is extension/test chase-friendly."""
    z = str(zone or "").upper()
    if z in CALL_CHASE_ZONES:
        return "CALL"
    if z in PUT_CHASE_ZONES:
        return "PUT"
    return ""


def tape_score_floor_for_zone(zone: str | None, sig_type: str | None, *, hard_min: int = 70) -> int:
    """Lower the gate to CHASE_TAPE_MIN_SCORE for momentum types in chase zones."""
    hard = int(hard_min)
    if chase_side_for_zone(zone) and is_momentum_tape_type(sig_type):
        return min(hard, CHASE_TAPE_MIN_SCORE)
    return hard


def signal_side_from_alert(alert: dict | None) -> str:
    if not alert:
        return ""
    cat = str(alert.get("category") or "").upper()
    if cat == "BULLISH":
        return "CALL"
    if cat == "BEARISH":
        return "PUT"
    bias = str(alert.get("bias") or "").upper()
    if bias in ("LONG", "BULL", "BULLISH"):
        return "CALL"
    if bias in ("SHORT", "BEAR", "BEARISH"):
        return "PUT"
    typ = str(alert.get("type") or "").upper()
    if typ.endswith("_BULL") or "BULL" in typ:
        return "CALL"
    if typ.endswith("_BEAR") or "BEAR" in typ:
        return "PUT"
    return ""


def _event_side(ev: dict) -> str:
    side = str(ev.get("side") or "").upper()
    if side in ("CALL", "PUT"):
        return side
    kind = str(ev.get("kind") or ev.get("tag") or ev.get("headline") or "").upper()
    if "CALL" in kind:
        return "CALL"
    if "PUT" in kind:
        return "PUT"
    return ""


def _event_ts(ev: dict, now_ts: float) -> float | None:
    raw = ev.get("ts")
    if raw is None:
        raw = ev.get("epoch")
    try:
        if raw is not None and raw != "":
            val = float(raw)
            if val > 1e12:
                val /= 1000.0
            return val
    except (TypeError, ValueError):
        pass
    iso = str(ev.get("timestamp") or ev.get("ts_iso") or "")
    if not iso:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def evaluate_wall_chase(
    events: list | None,
    *,
    now_ts: float,
    window_s: float = WALL_CHASE_WINDOW_S,
    min_events: int = 2,
) -> dict:
    """Score recent WALL_BREAK / WALL_MIGRATION prints into a chase read.

    Returns keys: active, side (CALL|PUT|''), n_events, window_s, events (kept).
    Requires ≥ min_events same-side prints inside window_s ending at now_ts.
    """
    empty = {
        "active": False,
        "side": "",
        "n_events": 0,
        "window_s": float(window_s),
        "events": [],
    }
    kept: list[dict] = []
    for ev in events or []:
        kind = str(ev.get("kind") or ev.get("tag") or "").upper()
        headline = str(ev.get("headline") or "")
        is_wall = (
            "WALL_BREAK" in kind
            or "WALL_MIGRATION" in kind
            or "BROKEN" in headline.upper()
            or "MIGRATED" in headline.upper()
        )
        if not is_wall:
            continue
        ts = _event_ts(ev, now_ts)
        if ts is None:
            continue
        age = now_ts - ts
        if age < 0 or age > float(window_s):
            continue
        side = _event_side(ev)
        if side not in ("CALL", "PUT"):
            continue
        kept.append({
            "ts": ts,
            "side": side,
            "kind": "BREAK" if ("BREAK" in kind or "Broken" in headline) else "MIGRATE",
            "headline": headline,
        })
    if not kept:
        return empty

    by_side: dict[str, list[dict]] = {"CALL": [], "PUT": []}
    for ev in kept:
        by_side[ev["side"]].append(ev)

    best_side = ""
    best_n = 0
    best_evs: list[dict] = []
    for side, evs in by_side.items():
        if len(evs) > best_n:
            best_n = len(evs)
            best_side = side
            best_evs = evs

    active = best_n >= int(min_events)
    return {
        "active": active,
        "side": best_side if active else "",
        "n_events": best_n if active else 0,
        "window_s": float(window_s),
        "events": best_evs if active else [],
    }


def should_promote_extension_watch(
    *,
    zone: str,
    sig_side: str,
    wall_chase: dict | None = None,
) -> str:
    """Return CALL WATCH / PUT WATCH / '' for extension+tape (and wall-chase) promotes.

    Holistic rule from the 2-week sample: when spot is through/testing the
    named wall and momentum tape agrees, the stance is a watch — not STAY OUT.
    Wall-chase active on the same side strengthens the same promote.
    """
    side = str(sig_side or "").upper()
    z = str(zone or "").upper()
    chase = wall_chase or {}
    chase_side = str(chase.get("side") or "").upper() if chase.get("active") else ""

    if side == "CALL" and (z in CALL_CHASE_ZONES or chase_side == "CALL"):
        if z in CALL_CHASE_ZONES or (chase_side == "CALL" and z not in PUT_CHASE_ZONES):
            return "CALL WATCH"
    if side == "PUT" and (z in PUT_CHASE_ZONES or chase_side == "PUT"):
        if z in PUT_CHASE_ZONES or (chase_side == "PUT" and z not in CALL_CHASE_ZONES):
            return "PUT WATCH"
    return ""


def wall_events_from_commentary(entries: list | None, *, limit: int = 40) -> list[dict]:
    """Project commentary rows into wall_events for evaluate_wall_chase."""
    out: list[dict] = []
    for e in (entries or [])[:limit]:
        tags = e.get("tags") or e.get("tags_json") or []
        if isinstance(tags, str):
            try:
                import json
                tags = json.loads(tags)
            except Exception:
                tags = [tags]
        tag_u = " ".join(str(t).upper() for t in (tags or []))
        headline = str(e.get("headline") or "")
        hl_u = headline.upper()
        kind = ""
        if "WALL_BREAK" in tag_u or "BROKEN" in hl_u:
            kind = "WALL_BREAK"
        elif "WALL_MIGRATION" in tag_u or "MIGRATED" in hl_u:
            kind = "WALL_MIGRATION"
        if not kind:
            continue
        side = "CALL" if "CALL" in hl_u or "CALL" in tag_u else (
            "PUT" if "PUT" in hl_u or "PUT" in tag_u else ""
        )
        if not side:
            continue
        ts = e.get("ts")
        epoch = None
        if isinstance(ts, (int, float)):
            epoch = float(ts)
        elif hasattr(ts, "timestamp"):
            try:
                epoch = float(ts.timestamp())
            except Exception:
                epoch = None
        elif isinstance(ts, str) and ts:
            try:
                from datetime import datetime
                epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                epoch = None
        out.append({
            "kind": kind,
            "side": side,
            "headline": headline,
            "ts": epoch,
            "timestamp": ts if isinstance(ts, str) else None,
        })
    return out
