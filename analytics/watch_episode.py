"""CALL/PUT WATCH episode ledger — trigger stamp + kill/target resolution.

Alert-tape WATCH is a map/tape stance, not an order. This module records when
a watch opened and later marks TARGET_HIT / INVALIDATED / EXPIRED from spot vs
explicit kill/target levels. Human confirms; no auto-execution.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime, timezone, timedelta
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
_LOCK = threading.Lock()
_INR_RE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")

# Soft defaults when kill/target text has no parseable strike
_DEFAULT_RISK = {"NIFTY": 40.0, "BANKNIFTY": 100.0, "SENSEX": 120.0}
_MIN_KILL_DIST = {"NIFTY": 25.0, "BANKNIFTY": 60.0, "SENSEX": 80.0}
_MAX_TARGET_DIST = {"NIFTY": 150.0, "BANKNIFTY": 350.0, "SENSEX": 400.0}
_DEFAULT_TARGET_MULT = 1.5
_MAX_AGE_S = 3 * 60 * 60  # expire open watches after 3h
_KILL_COOLDOWN_S = 5 * 60  # no same-side re-arm for 5m after INVALIDATED


def _f(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def side_from_stance(stance: str | None) -> str:
    """CALL / PUT from a WATCH stance; empty when STAY OUT or unknown."""
    s = str(stance or "").upper()
    if "CALL" in s and "WATCH" in s:
        return "CALL"
    if "PUT" in s and "WATCH" in s:
        return "PUT"
    return ""


def resolve_episode_side(
    *,
    stance: str | None,
    side: str | None = None,
    map_side: str | None = None,
) -> str | None:
    """Episode geometry side must match the WATCH stance.

    Returns CALL/PUT, or None when the caller should skip opening (map-side
    wall-chase disagrees with tape stance — 23-Nov side≠stance bug).
    When stance is a WATCH, it wins over a stale ``side`` argument; only
    ``map_side`` may veto.
    """
    stance_side = side_from_stance(stance)
    map_u = str(map_side or "").upper()
    if stance_side:
        if map_u in ("CALL", "PUT") and map_u != stance_side:
            return None
        return stance_side
    # Fall back to explicit side only when stance is not a WATCH.
    raw = str(side or map_side or "").upper()
    return raw if raw in ("CALL", "PUT") else None


def _parse_rupee_levels(text: str | None) -> list[float]:
    out: list[float] = []
    for m in _INR_RE.finditer(str(text or "")):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except (TypeError, ValueError):
            continue
    return out


def _journal_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    d = root / "data" / "journal" / "watch_episodes"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class WatchEpisode:
    episode_id: str
    symbol: str
    side: str  # CALL | PUT
    stance: str
    triggered_at: str  # ISO IST
    triggered_ts: float
    spot: float
    kill_level: float
    target_level: float
    kill_text: str = ""
    trigger_text: str = ""
    zone: str = ""
    source: str = "WALL_CHASE"
    status: str = "ACTIVE"  # ACTIVE | TARGET_HIT | INVALIDATED | EXPIRED
    resolved_at: str = ""
    resolved_ts: float = 0.0
    resolve_spot: float = 0.0
    mfe_pts: float = 0.0
    mae_pts: float = 0.0
    facts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def derive_watch_levels(
    *,
    symbol: str,
    side: str,
    spot: float,
    kill_text: str = "",
    trigger_text: str = "",
    stretch: dict | None = None,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    strike_step: float = 50.0,
    invalidation_level: float = 0.0,
) -> dict:
    """Pick kill (invalidation) and target strikes for a WATCH episode."""
    side_u = str(side or "").upper()
    sym_u = str(symbol or "NIFTY").upper()
    spot_f = _f(spot)
    step = max(_f(strike_step, 50.0), 1.0)
    stretch = stretch or {}
    risk = float(_DEFAULT_RISK.get(sym_u, 50.0))
    min_kill = float(_MIN_KILL_DIST.get(sym_u, risk * 0.6))
    max_tgt = float(_MAX_TARGET_DIST.get(sym_u, risk * 3.5))
    cw_f = _f(call_wall)
    pw_f = _f(put_wall)

    kill = _f(invalidation_level)
    if kill <= 0:
        parsed = _parse_rupee_levels(kill_text)
        if parsed:
            kill = parsed[-1]
    if kill <= 0:
        # Prefer first broken wall / last wall on the chase side
        first = _f(stretch.get("first_wall"))
        last = _f(stretch.get("last_wall"))
        if side_u == "CALL":
            kill = first or last or cw_f or (spot_f - risk)
        else:
            kill = first or last or pw_f or (spot_f + risk)

    target = _f(stretch.get("next_map_strike"))
    if target <= 0:
        last = _f(stretch.get("last_wall"))
        if side_u == "CALL":
            target = (last + step) if last > 0 else (spot_f + risk * _DEFAULT_TARGET_MULT)
        else:
            target = (last - step) if last > 0 else (spot_f - risk * _DEFAULT_TARGET_MULT)

    # Sanity + structural bounds:
    # - CALL: target above spot (capped to reachable intraday range), kill below spot (outside 1m noise).
    # - PUT: mirrored.
    if side_u == "CALL":
        if target <= spot_f:
            target = spot_f + max(step, risk)
        if (target - spot_f) > max_tgt:
            if spot_f < cw_f <= (spot_f + max_tgt):
                target = cw_f
            else:
                target = spot_f + max_tgt
        if kill >= spot_f:
            if 0 < pw_f < spot_f and (spot_f - pw_f) <= (risk * 2.5):
                kill = pw_f
            else:
                kill = spot_f - max(step, risk)
        if (spot_f - kill) < min_kill:
            kill = spot_f - min_kill
    elif side_u == "PUT":
        if target >= spot_f:
            target = spot_f - max(step, risk)
        if (spot_f - target) > max_tgt:
            if (spot_f - max_tgt) <= pw_f < spot_f:
                target = pw_f
            else:
                target = spot_f - max_tgt
        if kill <= spot_f:
            if cw_f > spot_f and (cw_f - spot_f) <= (risk * 2.5):
                kill = cw_f
            else:
                kill = spot_f + max(step, risk)
        if (kill - spot_f) < min_kill:
            kill = spot_f + min_kill

    return {
        "kill_level": round(kill, 1),
        "target_level": round(target, 1),
        "kill_text": str(kill_text or ""),
        "trigger_text": str(trigger_text or ""),
    }


def open_watch_episode(
    *,
    symbol: str,
    side: str,
    stance: str,
    spot: float,
    now_ist: datetime | None = None,
    kill_text: str = "",
    trigger_text: str = "",
    stretch: dict | None = None,
    call_wall: float = 0.0,
    put_wall: float = 0.0,
    strike_step: float = 50.0,
    invalidation_level: float = 0.0,
    zone: str = "",
    source: str = "WALL_CHASE",
    extra_facts: dict | None = None,
    map_side: str | None = None,
) -> WatchEpisode | None:
    """Create + persist an ACTIVE watch episode (caller dedupes rising edge).

    Side is taken from the WATCH stance. Kill/target geometry follows that
    side — never the opposite wall-chase map side. Returns None when map_side
    (or an explicit side) disagrees with stance.
    """
    now = now_ist or datetime.now(tz=IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    sym = str(symbol or "NIFTY").upper()
    facts = dict(extra_facts or {})
    map_u = str(map_side if map_side is not None else facts.get("map_side") or "").upper()
    side_u = resolve_episode_side(stance=stance, side=side, map_side=map_u or None)
    if not side_u:
        return None
    if map_u in ("CALL", "PUT"):
        facts["map_side"] = map_u
    # Reuse any already-ACTIVE same-side episode (prevents stacking multiple
    # episodes on one move while the initial thesis is still open).
    for prior in list_active_episodes(sym):
        if prior.side == side_u:
            return prior
    # Post-kill cooldown: do not immediately re-arm the same side after INVALIDATED.
    if _in_kill_cooldown(sym, side_u, now.timestamp()):
        return None
    levels = derive_watch_levels(
        symbol=sym,
        side=side_u,
        spot=spot,
        kill_text=kill_text,
        trigger_text=trigger_text,
        stretch=stretch,
        call_wall=call_wall,
        put_wall=put_wall,
        strike_step=strike_step,
        invalidation_level=invalidation_level,
    )
    ep = WatchEpisode(
        episode_id=f"watch-{sym.lower()}-{int(now.timestamp())}",
        symbol=sym,
        side=side_u,
        stance=str(stance or f"{side_u} WATCH"),
        triggered_at=now.strftime("%Y-%m-%d %H:%M:%S IST"),
        triggered_ts=float(now.timestamp()),
        spot=round(_f(spot), 2),
        kill_level=levels["kill_level"],
        target_level=levels["target_level"],
        kill_text=levels["kill_text"],
        trigger_text=levels["trigger_text"],
        zone=str(zone or ""),
        source=str(source or "WALL_CHASE"),
        status="ACTIVE",
        facts=facts,
    )
    _save_episode(ep)
    return ep


def evaluate_watch_episode(
    ep: WatchEpisode,
    *,
    spot: float,
    now_ts: float | None = None,
) -> WatchEpisode:
    """Update MFE/MAE; resolve TARGET_HIT / INVALIDATED / EXPIRED."""
    if ep.status != "ACTIVE":
        return ep
    spot_f = _f(spot)
    if spot_f <= 0:
        return ep
    now = float(now_ts if now_ts is not None else datetime.now(tz=IST).timestamp())
    # Another evaluator may have resolved this episode since our copy was read.
    # Saving our stale ACTIVE copy back used to resurrect it (25 Nov Sensex: a 11:06
    # CALL WATCH sat ACTIVE under five newer same-side episodes until 12:26).
    on_disk = load_episode(ep.episode_id)
    if on_disk is not None and on_disk.status != "ACTIVE":
        return on_disk
    # An episode belongs to one session. A spot from another day is not its price:
    # 23 Nov Bank Nifty watches were resolved at 09:38 the next morning with the
    # new day's spot, one of them TARGET_HIT with a +173 pt move that never happened.
    trig_day = datetime.fromtimestamp(_f(ep.triggered_ts), tz=IST).date()
    if datetime.fromtimestamp(now, tz=IST).date() != trig_day:
        close = datetime.combine(trig_day, dtime(15, 30), tzinfo=IST)
        ep.status = "EXPIRED"
        ep.resolved_ts = close.timestamp()
        ep.resolved_at = close.strftime("%Y-%m-%d %H:%M:%S IST")
        ep.resolve_spot = 0.0
        ep.facts = {**(ep.facts or {}), "expired_reason": "session ended unresolved; no cross-day spot used"}
        _save_episode(ep)
        return ep
    side = str(ep.side or "").upper()
    entry = _f(ep.spot)
    if side == "CALL":
        fav = spot_f - entry
        adv = entry - spot_f
        hit_tgt = (_f(ep.target_level) > 0) and (spot_f >= _f(ep.target_level))
        hit_kill = (_f(ep.kill_level) > 0) and (spot_f <= _f(ep.kill_level))
    else:
        fav = entry - spot_f
        adv = spot_f - entry
        hit_tgt = (_f(ep.target_level) > 0) and (spot_f <= _f(ep.target_level))
        hit_kill = (_f(ep.kill_level) > 0) and (spot_f >= _f(ep.kill_level))

    ep.mfe_pts = round(max(_f(ep.mfe_pts), fav), 1)
    # MAE stored negative (adverse excursion)
    if adv > 0:
        ep.mae_pts = round(min(_f(ep.mae_pts), -adv), 1)

    status = ""
    if hit_kill:
        status = "INVALIDATED"
    elif hit_tgt:
        status = "TARGET_HIT"
    elif (now - _f(ep.triggered_ts)) >= _MAX_AGE_S:
        status = "EXPIRED"

    if status:
        ep.status = status
        ep.resolved_ts = now
        ep.resolve_spot = round(spot_f, 2)
        try:
            ep.resolved_at = datetime.fromtimestamp(now, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")
        except Exception:
            ep.resolved_at = ""
        _save_episode(ep)
    else:
        _save_episode(ep)
    return ep


def _in_kill_cooldown(
    symbol: str,
    side: str,
    now_ts: float,
    *,
    cooldown_s: float = _KILL_COOLDOWN_S,
) -> bool:
    """True when a same-side episode was INVALIDATED within cooldown_s."""
    sym = str(symbol or "").upper()
    side_u = str(side or "").upper()
    if not sym or side_u not in ("CALL", "PUT") or cooldown_s <= 0:
        return False
    for path in _journal_path().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(data.get("symbol") or "").upper() != sym:
            continue
        if str(data.get("side") or "").upper() != side_u:
            continue
        if str(data.get("status") or "").upper() != "INVALIDATED":
            continue
        rts = _f(data.get("resolved_ts"))
        if rts > 0 and 0.0 <= (now_ts - rts) < float(cooldown_s):
            return True
    return False


def list_active_episodes(symbol: str | None = None) -> list[WatchEpisode]:
    sym = (symbol or "").upper() or None
    out: list[WatchEpisode] = []
    for path in sorted(_journal_path().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ep = WatchEpisode(**{k: data[k] for k in WatchEpisode.__dataclass_fields__ if k in data})
        except Exception:
            continue
        if ep.status != "ACTIVE":
            continue
        if sym and ep.symbol != sym:
            continue
        out.append(ep)
    return out


def load_episode(episode_id: str) -> WatchEpisode | None:
    path = _journal_path() / f"{episode_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return WatchEpisode(**{k: data[k] for k in WatchEpisode.__dataclass_fields__ if k in data})
    except Exception:
        return None


def _save_episode(ep: WatchEpisode) -> None:
    path = _journal_path() / f"{ep.episode_id}.json"
    with _LOCK:
        if ep.status == "ACTIVE" and path.exists():
            # Never write an ACTIVE copy over a resolved file.
            try:
                disk_status = str(json.loads(path.read_text(encoding="utf-8")).get("status") or "")
            except Exception:
                disk_status = ""
            if disk_status and disk_status != "ACTIVE":
                return
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(ep.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, path)


def evaluate_open_watches_for_symbol(
    symbol: str,
    *,
    spot: float,
    now_ts: float | None = None,
) -> list[WatchEpisode]:
    """Evaluate all ACTIVE watches for one index; return those that newly resolved."""
    resolved: list[WatchEpisode] = []
    for ep in list_active_episodes(symbol):
        before = ep.status
        updated = evaluate_watch_episode(ep, spot=spot, now_ts=now_ts)
        if before == "ACTIVE" and updated.status != "ACTIVE":
            resolved.append(updated)
    return resolved
