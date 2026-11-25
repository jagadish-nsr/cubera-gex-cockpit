"""Alert tape: geometry-first stance + confidence for a naked option buyer.

Walls are OI/gamma concentration under an unvalidated India dealer-sign model.
This module never claims a proven dealer hedge book. Sector breadth is diagnostics
only — not a trend trigger. Each print opens with STAY OUT / CALL WATCH / PUT WATCH,
a HIGH/MED/LOW grade, a concrete trigger, and a kill level. No named buy strike.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any


SIGNAL_MIN_SCORE = 70
SIGNAL_MAX_AGE_S = 300.0
TAPE_HOLD_S = 75.0
IST = timezone(timedelta(hours=5, minutes=30))

# Soft floor for CONTINUATION_/CVD_DIVERGENCE_ in chase zones (2-week sample:
# 6/20 STRONG wall-chase episodes peaked at 65–69 and missed CALL WATCH).
from analytics.wall_chase import (  # noqa: E402
    CHASE_TAPE_MIN_SCORE,
    chase_side_for_zone,
    evaluate_wall_chase,
    should_promote_extension_watch,
    tape_score_floor_for_zone,
)
from analytics.map_lag_run import evaluate_map_lag_run  # noqa: E402


def _f(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _inr(val: float) -> str:
    return f"₹{float(val):,.0f}"


def _hms(text: str) -> str:
    match = re.search(r"(\d{2}:\d{2}:\d{2})", text or "")
    return match.group(1) if match else ""


def _display_clock(raw: str, now_ts: float) -> str:
    """A tape clock with its date. A bare HH:MM:SS takes the date of now_ts."""
    text = str(raw or "").strip()
    dated = re.fullmatch(r"(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2}:\d{2})(?:\s+IST)?", text)
    if dated:
        return f"{dated.group(1)} {dated.group(2)} IST"
    bare = re.fullmatch(r"(\d{2}:\d{2}:\d{2})(?:\s+IST)?", text)
    if bare:
        day = datetime.fromtimestamp(now_ts, tz=IST).strftime("%d-%m-%Y")
        return f"{day} {bare.group(1)} IST"
    if not text:
        return datetime.fromtimestamp(now_ts, tz=IST).strftime("%d-%m-%Y %H:%M:%S IST")
    return text if text.endswith("IST") else f"{text} IST"


def classify_zone(
    spot: float,
    call_wall: float,
    put_wall: float,
    step: float,
) -> dict:
    """Locate spot vs call/put GEX concentration. step = index strike interval."""
    spot = _f(spot)
    cw = _f(call_wall)
    pw = _f(put_wall)
    step = max(_f(step, 50.0), 1.0)
    if spot <= 0:
        return {"zone": "UNKNOWN", "label": "No spot", "through_pts": 0.0, "step": step}

    same_wall = cw > 0 and pw > 0 and abs(cw - pw) <= max(0.51, step * 0.01)
    if same_wall:
        mag = cw
        dist = spot - mag
        if dist < -step:
            return {
                "zone": "BELOW_MAGNET",
                "label": "Below GEX magnet",
                "through_pts": mag - spot,
                "step": step,
                "magnet": mag,
            }
        if dist > step:
            return {
                "zone": "ABOVE_MAGNET",
                "label": "Above GEX magnet",
                "through_pts": spot - mag,
                "step": step,
                "magnet": mag,
            }
        return {
            "zone": "TEST_MAGNET",
            "label": "At GEX magnet",
            "through_pts": abs(dist),
            "step": step,
            "magnet": mag,
        }

    # A corridor exists only when the put wall is below the call wall.
    # Crossed walls (put above call) are not a range: name a wall price is
    # actually touching, otherwise leave the map unclear.
    oriented = cw > pw > 0
    crossed = pw > cw > 0
    if not crossed and pw > 0 and spot <= pw - step:
        return {
            "zone": "BELOW_PUT_WALL",
            "label": "Below put-wall concentration",
            "through_pts": pw - spot,
            "step": step,
        }
    if not crossed and cw > 0 and spot >= cw + step:
        return {
            "zone": "ABOVE_CALL_WALL",
            "label": "Above call-wall concentration",
            "through_pts": spot - cw,
            "step": step,
        }
    # Two listed strikes leave no interior that is clear of both walls.
    # A one-point wiggle around the midpoint would otherwise flip the headline.
    if oriented and (cw - pw) <= (2 * step) and pw < spot < cw:
        return {
            "zone": "PINNED",
            "label": "Between wall concentrations",
            "through_pts": 0.0,
            "step": step,
        }
    near_put = pw > 0 and abs(spot - pw) <= step
    near_call = cw > 0 and abs(spot - cw) <= step
    if near_put and near_call and abs(spot - cw) < abs(spot - pw):
        near_put = False
    if near_put:
        return {
            "zone": "TEST_PUT",
            "label": "Testing put-wall concentration",
            "through_pts": abs(spot - pw),
            "step": step,
        }
    if near_call:
        return {
            "zone": "TEST_CALL",
            "label": "Testing call-wall concentration",
            "through_pts": abs(spot - cw),
            "step": step,
        }
    if oriented and pw < spot < cw:
        return {
            "zone": "PINNED",
            "label": "Between wall concentrations",
            "through_pts": 0.0,
            "step": step,
        }
    return {
        "zone": "UNSITUATED",
        "label": (
            "Put wall above the call wall"
            if crossed
            else "Walls not a clean corridor"
        ),
        "through_pts": 0.0,
        "step": step,
    }


def _alert_epoch(alert: dict, now_ts: float) -> float | None:
    raw_ts = alert.get("ts") or alert.get("bar_start_ts")
    if raw_ts is not None:
        if isinstance(raw_ts, (int, float)):
            val = float(raw_ts)
            if val > 1e12:
                val /= 1000.0
            return val
        if isinstance(raw_ts, str) and raw_ts:
            try:
                dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=IST)
                return dt.timestamp()
            except ValueError:
                pass
    clock = str(alert.get("time") or "").strip()
    if not clock:
        return None
    try:
        hh, mm, ss = (clock.split()[0]).split(":")
        now = datetime.fromtimestamp(now_ts, tz=IST)
        dt = now.replace(hour=int(hh), minute=int(mm), second=int(float(ss)), microsecond=0)
        return dt.timestamp()
    except (ValueError, IndexError):
        return None


def pick_fresh_signal(
    alerts: list | None,
    now_ts: float,
    *,
    min_score: int = SIGNAL_MIN_SCORE,
    max_age_s: float = SIGNAL_MAX_AGE_S,
    zone: str | None = None,
) -> dict | None:
    """Newest live Sentinel print. Ignores FAILED/REPLACED.

    When ``zone`` is chase-friendly (through/testing a named wall), momentum
    tape types (CONTINUATION_*/CVD_DIVERGENCE_*) qualify from
    :data:`CHASE_TAPE_MIN_SCORE` (65). Other types keep the hard floor (70).
    """
    best = None
    best_ts = -1.0
    for alert in alerts or []:
        status = str(alert.get("status") or "NEW").upper()
        if status in ("FAILED", "REPLACED"):
            continue
        try:
            score = int(alert.get("score") or 0)
        except (TypeError, ValueError):
            continue
        floor = tape_score_floor_for_zone(
            zone, str(alert.get("type") or ""), hard_min=min_score,
        )
        if score < floor:
            continue
        ts = _alert_epoch(alert, now_ts)
        if ts is None:
            continue
        age = now_ts - ts
        if age < 0 or age > max_age_s:
            continue
        if ts >= best_ts:
            best_ts = ts
            best = dict(alert)
            best["_ts"] = ts
            best["_age_s"] = age
            best["_score_floor"] = floor
    return best


def _book_line(regime: str) -> str:
    word = str(regime or "").upper()
    if word == "POSITIVE":
        return "Call-heavy: calls hold more gamma."
    if word == "NEGATIVE":
        return "Put-heavy: puts hold more gamma."
    return ""


def _breadth_sentence(breadth: dict | None) -> str:
    b = breadth or {}
    adv = int(b.get("advances") or 0)
    dec = int(b.get("declines") or 0)
    if adv == 0 and dec == 0:
        return ""
    return f"Breadth is {adv} up and {dec} down."


def _say(*parts: str) -> str:
    return " ".join(p.strip() for p in parts if p and str(p).strip())


def _signal_label(sig: dict) -> str:
    raw = str(sig.get("type") or "SIGNAL").replace("_", " ")
    return raw


DECISIVE_ZONES = frozenset({
    "BELOW_MAGNET",
    "ABOVE_MAGNET",
    "BELOW_PUT_WALL",
    "ABOVE_CALL_WALL",
    "TEST_PUT",
    "TEST_CALL",
})
MIDDLE_ZONES = frozenset({"TEST_MAGNET", "PINNED", "UNSITUATED", "UNKNOWN"})


def _signal_side(sig: dict | None) -> str:
    """Return CALL, PUT, or '' from a fresh Sentinel category."""
    if not sig:
        return ""
    cat = str(sig.get("category") or "").upper()
    if cat == "BULLISH":
        return "CALL"
    if cat == "BEARISH":
        return "PUT"
    return ""


def _grade_confidence(
    *,
    stance: str,
    zone: str,
    sig: dict | None,
    session_closed: bool,
) -> tuple[str, list[str]]:
    """Return (HIGH|MED|LOW, reason lines). STAY OUT cannot be HIGH."""
    reasons: list[str] = []
    decisive = (not session_closed) and zone in DECISIVE_ZONES
    fresh = bool(sig)
    side = _signal_side(sig)
    agrees = (
        (stance == "CALL WATCH" and side == "CALL")
        or (stance == "PUT WATCH" and side == "PUT")
    )

    bits = 0
    if decisive:
        bits += 1
        reasons.append(f"Zone {zone.replace('_', ' ').title()} is decisive.")
    else:
        reasons.append(
            "Zone is not decisive."
            if not session_closed
            else "Session is closed — no live trade window."
        )

    if fresh:
        bits += 1
        floor = int((sig or {}).get("_score_floor") or SIGNAL_MIN_SCORE)
        reasons.append(f"Fresh orderflow print ≥{floor}: {_signal_label(sig)}.")
    else:
        floor = CHASE_TAPE_MIN_SCORE if chase_side_for_zone(zone) else SIGNAL_MIN_SCORE
        reasons.append(f"No fresh orderflow print ≥{floor}.")

    if stance in ("CALL WATCH", "PUT WATCH") and agrees:
        bits += 1
        reasons.append(f"{_signal_label(sig)} agrees with {stance}.")
    elif stance in ("CALL WATCH", "PUT WATCH"):
        reasons.append(f"Orderflow does not agree with {stance}.")
    elif zone in MIDDLE_ZONES or session_closed:
        reasons.append("Middle map or off-hours — agreement bit is off.")
    else:
        reasons.append("STAY OUT — agreement bit is off.")

    if stance == "STAY OUT":
        grade = "MED" if bits >= 2 or decisive else "LOW"
    elif bits >= 3:
        grade = "HIGH"
    elif bits >= 2 or decisive:
        grade = "MED"
    else:
        grade = "LOW"
    return grade, reasons


def _risk_line(trigger: str, kill: str, *, watch: bool) -> str:
    stop = " Stop beyond the wall, never at the magnet." if watch else ""
    return f"Trigger: {trigger} Kill: {kill}.{stop}".replace("..", ".")


_FORBIDDEN_ALERT_PHRASES = (
    "buy now", "sell now", "market order", "auto-exec", "auto exec",
    "place order", "enter long", "enter short",
)


def _alert_rewrite_ok(alert: dict, headline: str, drivers: str) -> bool:
    """Reject rewrites that drop stance/kill or invent execution language."""
    if not headline or not drivers or len(drivers) < 20:
        return False
    low = f"{headline} {drivers}".lower()
    if any(p in low for p in _FORBIDDEN_ALERT_PHRASES):
        return False
    stance = str(alert.get("stance") or "")
    # Stance/kill stay on structured fields; rewrite must not contradict by claiming another stance.
    other = {"STAY OUT", "CALL WATCH", "PUT WATCH"} - {stance}
    if any(s.lower() in low for s in other):
        return False
    return True


# Cache polished prose so cockpit-data polls don't re-hit Ollama every few seconds.
_ALERT_LLM_CACHE: dict[str, tuple[float, dict]] = {}
_ALERT_LLM_CACHE_TTL_S = 120.0


def _alert_polish_cache_key(alert: dict) -> str:
    return "|".join([
        str(alert.get("scenario") or ""),
        str(alert.get("stance") or ""),
        str(alert.get("confidence") or ""),
        str(alert.get("headline") or ""),
        str(alert.get("drivers") or ""),
        str(alert.get("zone") or ""),
    ])


def _maybe_llm_polish_alert(alert: dict) -> dict:
    """Optional LLM polish of headline/drivers; fail-soft to template."""
    if not isinstance(alert, dict):
        return alert
    alert = dict(alert)
    alert.setdefault("text_source", "heuristic")
    alert.setdefault("llm_model", None)
    try:
        from analytics.llm_client import complete_chat, llm_enabled, model_name
        from analytics.llm_prompts import alert_tape_messages, parse_alert_tape_rewrite
        if not llm_enabled("alert_tape"):
            return alert
        import time as _time
        key = _alert_polish_cache_key(alert)
        now = _time.time()
        hit = _ALERT_LLM_CACHE.get(key)
        if hit and (now - hit[0]) < _ALERT_LLM_CACHE_TTL_S:
            cached = dict(hit[1])
            # Keep live timestamp/badge/theme from this evaluation.
            for k in ("timestamp", "badge", "theme", "outcome", "body", "risk",
                      "action", "trigger", "kill", "through_pts", "signal_type",
                      "signal_score"):
                if k in alert:
                    cached[k] = alert[k]
            return cached
        raw = complete_chat(
            alert_tape_messages(alert),
            purpose="alert_tape",
            temperature=0.2,
            max_tokens=320,
            timeout_s=90.0,
        )
        parsed = parse_alert_tape_rewrite(raw or "")
        if not parsed:
            return alert
        headline, drivers = parsed
        if not _alert_rewrite_ok(alert, headline, drivers):
            return alert
        alert["headline"] = headline
        alert["drivers"] = drivers
        alert["diagnostics"] = drivers
        alert["text_source"] = "genai"
        alert["llm_model"] = model_name()
        _ALERT_LLM_CACHE[key] = (now, dict(alert))
        if len(_ALERT_LLM_CACHE) > 64:
            # Drop oldest entries.
            for old_key, _ in sorted(_ALERT_LLM_CACHE.items(), key=lambda kv: kv[1][0])[:16]:
                _ALERT_LLM_CACHE.pop(old_key, None)
        return alert
    except Exception:  # noqa: BLE001
        return alert


def evaluate_market_alert(dash: dict, *, now_ts: float | None = None) -> dict | None:
    """Compose one alert-tape message from cockpit payload fields."""
    if not dash:
        return None
    kpis = dash.get("kpis") or {}
    spot = _f(dash.get("spot"))
    if spot <= 0:
        return None
    cw = _f((kpis.get("call_wall") or {}).get("val"))
    pw = _f((kpis.get("put_wall") or {}).get("val"))
    flip = _f((kpis.get("gamma_flip") or {}).get("raw") or (kpis.get("gamma_flip") or {}).get("val") or dash.get("gamma_flip"))
    step = _f(dash.get("strike_step"), 50.0)
    regime = str((kpis.get("regime") or {}).get("val") or "NEUTRAL").upper()
    blast = dash.get("gamma_blast") or {}
    try:
        blast_score = int(blast.get("score") or 0)
    except (TypeError, ValueError):
        blast_score = 0
    # Loud EXPIRY BLAST only for non-pin expansion ACTIVE. PIN/COMPRESS is a
    # concentration map (Sensex 19 Nov) — do not siren it as a blast.
    is_expiry_blast = False
    is_expiry_pin = False
    try:
        from app.gammablast import blast_expansion_trusted, blast_pin_trusted
        is_expiry_blast = blast_expansion_trusted(blast)
        is_expiry_pin = (not is_expiry_blast) and blast_pin_trusted(blast)
    except Exception:
        is_expiry_blast = bool(blast.get("is_expiry")) and blast_score >= 60 and (
            blast.get("cover_known") is True
            and blast.get("pin_geometry") is not True
            and "PIN" not in str(blast.get("badge") or "").upper()
        )
    breadth = dash.get("market_breadth") or {}
    now_ts = float(now_ts if now_ts is not None else datetime.now(tz=IST).timestamp())

    time_str = _display_clock(str(dash.get("as_of_time") or ""), now_ts)
    snap_as_of = str(dash.get("snapshot_as_of") or "").strip()
    phase = str(dash.get("session_phase") or "")
    sched = dash.get("session_schedule") or {}
    is_muhurat = str(sched.get("session_kind") or "") == "muhurat"
    is_pre_bell = bool(dash.get("is_pre_bell_ephemeral")) or phase in (
        "PRE_MARKET_ENTRY", "PRE_MARKET_DISCOVERY",
    )
    is_morning = phase == "PRE_MARKET_SETUP" or bool(dash.get("is_morning_briefing"))
    is_closed = dash.get("is_market_open") is False or str(dash.get("market_state") or "") == "CLOSED"
    open_hm = "14:20" if is_muhurat else "09:15"
    pre_hm = "14:05" if is_muhurat else "09:00"
    disc_hm = "14:12" if is_muhurat else "09:08"
    geo = classify_zone(spot, cw, pw, step)
    zone = geo["zone"]
    near_flip = flip > 0 and abs(spot - flip) <= step
    wall_chase = evaluate_wall_chase(dash.get("wall_events"), now_ts=now_ts)
    map_lag = evaluate_map_lag_run(
        symbol=str(dash.get("symbol") or "NIFTY"),
        spot_path=dash.get("spot_path"),
        zone=zone,
        wall_events=dash.get("wall_events"),
        orderflow_alerts=dash.get("orderflow_alerts"),
        now_ts=now_ts,
        wall_chase=wall_chase,
    ) if not (is_pre_bell or is_closed) else {
        "active": False, "side": "", "move_pts": 0.0, "reasons": [], "reason_n": 0,
    }
    sig = None if (is_pre_bell or is_closed) else pick_fresh_signal(
        dash.get("orderflow_alerts"), now_ts, zone=zone,
    )
    book = _book_line(regime)
    breadth_line = _breadth_sentence(breadth)
    if flip > 0 and near_flip:
        flip_line = f"The flip is {_inr(flip)}, one strike from price."
    elif flip > 0:
        flip_line = f"The flip is {_inr(flip)}."
    else:
        flip_line = ""
    snap_clause = ""
    if snap_as_of and _hms(snap_as_of) != _hms(time_str):
        snap_clause = f"The gamma snapshot is from {snap_as_of}."

    def pack(**kwargs) -> dict:
        out = {
            "timestamp": time_str,
            "zone": zone,
            "through_pts": round(float(geo.get("through_pts") or 0.0), 1),
            "signal_type": (sig or {}).get("type") or "",
            "signal_score": int((sig or {}).get("score") or 0),
            "wall_chase": bool(wall_chase.get("active")),
            "wall_chase_side": wall_chase.get("side") or "",
            "wall_chase_n": int(wall_chase.get("n_events") or 0),
            "map_lag": bool(map_lag.get("active")),
            "map_lag_side": map_lag.get("side") or "",
            "map_lag_move": float(map_lag.get("move_pts") or 0.0),
            "map_lag_reasons": list(map_lag.get("reasons") or []),
        }
        out.update(kwargs)
        return out

    def _voice(
        scenario,
        badge,
        theme,
        headline,
        *,
        stance: str,
        confidence: str,
        where: str,
        drivers: str,
        trigger: str,
        kill: str,
        conf_reasons: list[str] | None = None,
    ):
        watch = stance in ("CALL WATCH", "PUT WATCH")
        outcome = f"{stance} · Confidence {confidence}. {where}".strip()
        reason_bits = " ".join(conf_reasons or [])
        drivers_full = _say(drivers, reason_bits)
        risk = _risk_line(trigger, kill, watch=watch)
        badge_out = f"{badge} · {confidence}"
        out = pack(
            scenario=scenario,
            badge=badge_out,
            theme=theme,
            headline=headline,
            meaning="",
            stance=stance,
            confidence=confidence,
            trigger=trigger,
            kill=kill,
            outcome=outcome,
            drivers=drivers_full,
            risk=risk,
            body=outcome,
            action=risk,
            diagnostics=drivers_full,
        )
        # Map-side chase must agree with WATCH stance for notify consumers.
        # Opposite wall migrations + opposing tape stay visible as STAY OUT /
        # plain WATCH — not as a wall-chase rising edge (23-Nov side≠stance).
        ws = str(out.get("wall_chase_side") or "")
        if watch and ws:
            want = "CALL" if stance == "CALL WATCH" else "PUT"
            if ws != want:
                out["wall_chase"] = False
                badge_clean = str(out.get("badge") or "")
                if "WALL CHASE" in badge_clean:
                    parts = [p for p in badge_clean.split(" · ") if "WALL CHASE" not in p]
                    out["badge"] = " · ".join(parts) if parts else badge_clean
        return _maybe_llm_polish_alert(out)

    if is_morning:
        pm = dash.get("pre_market") or {}
        from data.market_snapshot import format_ist_stamp
        oi_raw = str(pm.get("oi_as_of_display") or pm.get("oi_as_of") or snap_as_of or "")
        oi = format_ist_stamp(oi_raw) or oi_raw or "unavailable"
        stale_book = bool(pm.get("snapshot_stale"))
        fii = pm.get("fii_bias") or "—"
        cvd_raw = pm.get("prior_session_cvd")
        try:
            cvd_txt = f"{float(cvd_raw):+,.0f}" if cvd_raw is not None and cvd_raw != "" else "—"
        except (TypeError, ValueError):
            cvd_txt = "—"
        brief = str(pm.get("status_text") or "").strip() or (
            f"The last close stands against call wall {_inr(cw)}, put wall {_inr(pw)}, and flip {_inr(flip)}."
        )
        extras = [f"Open interest is as of {oi}."]
        if fii and fii != "—":
            extras.append(f"FII bias is {fii}.")
        if cvd_txt != "—":
            extras.append(f"Yesterday's futures delta was {cvd_txt}.")
        if stale_book:
            where = (
                "Yesterday's book is withheld — the saved snapshot is not the last close."
            )
        else:
            where = (
                f"This is yesterday's book. Options do not open until {open_hm}."
            )
        conf, reasons = _grade_confidence(
            stance="STAY OUT", zone=zone, sig=None, session_closed=True,
        )
        return _voice(
            "MORNING_BRIEFING",
            "🌅 YESTERDAY'S BOOK",
            "alert-tape-spark",
            "Before the open",
            stance="STAY OUT",
            confidence=conf,
            where=where,
            drivers=_say(brief, f"Call wall {_inr(cw)}. Put wall {_inr(pw)}.", flip_line, book, *extras),
            trigger=(
                f"Cash auction at {pre_hm}; opening print near {disc_hm}, then options at {open_hm}."
            ),
            kill=f"Any fill before {open_hm} is a guess",
            conf_reasons=reasons,
        )

    if is_pre_bell:
        discovered = bool((dash.get("pre_market") or {}).get("is_discovered"))
        conf, reasons = _grade_confidence(
            stance="STAY OUT", zone=zone, sig=None, session_closed=True,
        )
        return _voice(
            "PRE_OPEN_SETUP",
            "🔔 OPENING PRICE IN" if discovered else "🔔 PRE-OPEN",
            "alert-tape-warn",
            "Pre-open auction",
            stance="STAY OUT",
            confidence=conf,
            where=(
                f"Auction still runs on yesterday's walls; options open at {open_hm}."
            ),
            drivers=_say(
                f"The auction runs from {pre_hm} to {open_hm}. Call wall {_inr(cw)}. Put wall {_inr(pw)}.",
                flip_line,
                f"The opening price prints near {disc_hm}. Price on the screen is {_inr(spot)}.",
                book,
                snap_clause,
            ),
            trigger=(
                f"Compare the {disc_hm} print with these walls, then wait for {open_hm}."
                if discovered
                else f"Wait for the {disc_hm} print, then options at {open_hm}."
            ),
            kill=f"Any fill before {open_hm} is a guess",
            conf_reasons=reasons,
        )

    if is_closed:
        conf, reasons = _grade_confidence(
            stance="STAY OUT", zone=zone, sig=None, session_closed=True,
        )
        return _voice(
            "MARKET_CLOSED",
            "🌙 SESSION CLOSED",
            "alert-tape-spark",
            "Session closed",
            stance="STAY OUT",
            confidence=conf,
            where="Cash closed at 15:30. This is the last snapshot.",
            drivers=_say(
                f"Price is {_inr(spot)}. Call wall {_inr(cw)}. Put wall {_inr(pw)}.",
                flip_line,
                book,
                snap_clause,
            ),
            trigger="Next session open. Plan against these walls overnight.",
            kill="A new overnight buy can gap against you while premium decays",
            conf_reasons=reasons,
        )

    pts = f"{geo['through_pts']:.0f}"
    magnet_raw = float(geo.get("magnet") or cw or pw or 0.0)
    magnet = _inr(magnet_raw)
    sig_side = _signal_side(sig)

    zone_meta = {
        "BELOW_MAGNET": {
            "scenario": "BELOW_SUPPORT",
            "badge": "📉 BELOW THE CLUSTER",
            "theme": "alert-tape-stop",
            "headline": "Price is under the only big gamma strike",
            "stance": "STAY OUT",
            "where": (
                f"The only big gamma strike is up at {magnet}, {pts} points above price."
            ),
            "drivers": _say(
                "Calls and puts are piled on that one strike.",
                book, flip_line, breadth_line,
            ),
            "trigger": f"Reclaim above {magnet}.",
            "kill": f"Still below {magnet}",
        },
        "ABOVE_MAGNET": {
            "scenario": "ABOVE_RESISTANCE",
            "badge": "🔥 ABOVE THE CLUSTER",
            "theme": "alert-tape-spark",
            "headline": "Price is over the only big gamma strike",
            "stance": "STAY OUT",
            "where": (
                f"Price is {pts} points above the only big gamma strike at {magnet}."
            ),
            "drivers": _say("Calls and puts share that one strike.", book, flip_line, breadth_line),
            "trigger": f"Pull back within one strike of {magnet}.",
            "kill": f"Further extension away from {magnet} without tape",
        },
        "TEST_MAGNET": {
            "scenario": "TEST_MAGNET",
            "badge": "⚖️ ONE STRIKE",
            "theme": "alert-tape-warn",
            "headline": "Call and put gamma share one strike",
            "stance": "STAY OUT",
            "where": (
                f"Call gamma and put gamma sit on one strike, {magnet}, so there is no range."
            ),
            "drivers": _say(book, flip_line, breadth_line),
            "trigger": "Wait until the call strike and the put strike separate.",
            "kill": "Sitting on the one-strike map",
        },
        "BELOW_PUT_WALL": {
            "scenario": "BELOW_SUPPORT",
            "badge": "📉 UNDER PUT WALL",
            "theme": "alert-tape-stop",
            "headline": "Put wall is behind price",
            "stance": "STAY OUT",
            "where": (
                f"The put wall at {_inr(pw)} is already {pts} points above price."
            ),
            "drivers": _say(f"The call wall is {_inr(cw)}.", flip_line, book, breadth_line),
            "trigger": f"Reclaim above {_inr(pw)}.",
            "kill": f"Still below {_inr(pw)}",
        },
        "ABOVE_CALL_WALL": {
            "scenario": "ABOVE_RESISTANCE",
            "badge": "🔥 OVER CALL WALL",
            "theme": "alert-tape-spark",
            "headline": "Call wall is behind price",
            "stance": "STAY OUT",
            "where": (
                f"Price is {pts} points above the call wall at {_inr(cw)}."
            ),
            "drivers": _say(f"The put wall is {_inr(pw)}.", flip_line, book, breadth_line),
            "trigger": f"Pull back within one strike of {_inr(cw)}.",
            "kill": f"Further extension above {_inr(cw)} without tape",
        },
        "TEST_PUT": {
            "scenario": "TEST_PUT",
            "badge": "🚨 TESTING PUT WALL",
            "theme": "alert-tape-stop",
            "headline": "Price is at the put wall",
            "stance": "PUT WATCH" if sig_side == "PUT" else "STAY OUT",
            "where": f"Price is at the put wall {_inr(pw)}.",
            "drivers": _say(
                f"Price is within one strike of {_inr(pw)}. The call wall is {_inr(cw)}.",
                flip_line, book, breadth_line,
            ),
            "trigger": (
                f"Hold below {_inr(pw)} with tape agreement."
                if sig_side == "PUT"
                else f"A put only stays in play after a hold below {_inr(pw)} with tape agreement."
            ),
            "kill": f"Reclaim above {_inr(pw)}",
        },
        "TEST_CALL": {
            "scenario": "TEST_CALL",
            "badge": "🔥 TESTING CALL WALL",
            "theme": "alert-tape-spark",
            "headline": "Price is at the call wall",
            "stance": "CALL WATCH" if sig_side == "CALL" else "STAY OUT",
            "where": f"Price is at the call wall {_inr(cw)}.",
            "drivers": _say(
                f"Price is within one strike of {_inr(cw)}. The put wall is {_inr(pw)}.",
                flip_line, book, breadth_line,
            ),
            "trigger": (
                f"5-minute hold above {_inr(cw)} with tape agreement."
                if sig_side == "CALL"
                else f"A call only stays in play after a 5-minute hold above {_inr(cw)} with tape agreement."
            ),
            "kill": f"Fail back below {_inr(cw)}",
        },
        "PINNED": {
            "scenario": "SIDEWAYS_CHOP",
            "badge": "⚖️ BETWEEN WALLS",
            "theme": "alert-tape-warn",
            "headline": "Price is between the walls",
            "stance": "STAY OUT",
            "where": (
                f"Price sits between the put wall {_inr(pw)} and the call wall {_inr(cw)}."
            ),
            "drivers": _say(flip_line, book, breadth_line),
            "trigger": f"Clear {_inr(cw)} for a call watch, or clear {_inr(pw)} for a put watch.",
            "kill": "Sitting between the walls",
        },
        "UNSITUATED": {
            "scenario": "UNSITUATED",
            "badge": "⚖️ WALLS UNCLEAR",
            "theme": "alert-tape-warn",
            "headline": "No clean range around price",
            "stance": "STAY OUT",
            "where": "The call strike and the put strike do not form a range around price.",
            "drivers": _say(
                f"Price is {_inr(spot)}. Call wall {_inr(cw)}. Put wall {_inr(pw)}.",
                flip_line, book, breadth_line,
            ),
            "trigger": "Wait until the strikes line up. Until then the orderflow tape is the only read.",
            "kill": "Premium paid on an unclear map",
        },
        "UNKNOWN": {
            "scenario": "UNSITUATED",
            "badge": "⚖️ NO PRICE",
            "theme": "alert-tape-warn",
            "headline": "Waiting for a live quote",
            "stance": "STAY OUT",
            "where": "No live price against the walls.",
            "drivers": "The walls may be on the screen, but price is missing.",
            "trigger": "Wait for a live price.",
            "kill": "A premium paid without a live price is a guess",
        },
    }
    base = dict(zone_meta.get(zone) or zone_meta["UNSITUATED"])
    if zone == "UNSITUATED" and pw > cw > 0:
        base["headline"] = "Put wall is above the call wall"
        base["where"] = (
            f"The put wall {_inr(pw)} sits above the call wall {_inr(cw)}, so they do not form a range."
        )

    # Fresh signal on a middle zone: upgrade matching side to WATCH; mismatch stays out.
    # Avoid introducing a second conflicting wall level when the signal carries an explicit invalidation.
    sig_inval = _f(sig.get("invalidation_level")) if sig else 0.0
    if sig and zone in MIDDLE_ZONES:
        if sig_side == "CALL":
            base["stance"] = "CALL WATCH"
            base["trigger"] = (
                "Hold the bullish tape idea while above invalidation."
                if sig_inval > 0
                else f"Hold the bullish tape idea; clear {_inr(cw)} if walls are split."
            )
        elif sig_side == "PUT":
            base["stance"] = "PUT WATCH"
            base["trigger"] = (
                "Hold the bearish tape idea while below invalidation."
                if sig_inval > 0
                else f"Hold the bearish tape idea; clear {_inr(pw)} if walls are split."
            )

    # Extension / wall-chase promote (2-week sample): ABOVE_CALL / ABOVE_MAGNET
    # with CONT_BULL must not stay STAY OUT — that blocked Sensex 19 Nov 14:00.
    promoted = should_promote_extension_watch(
        zone=zone, sig_side=sig_side, wall_chase=wall_chase,
    )
    if promoted and base.get("stance") == "STAY OUT":
        base["stance"] = promoted
        if promoted == "CALL WATCH":
            base["trigger"] = (
                f"Hold above {_inr(cw or magnet_raw)} with tape agreement; "
                "stop beyond the last broken call wall, never at the magnet."
            )
            base["kill"] = f"Fail back below {_inr(cw or magnet_raw)}"
        else:
            base["trigger"] = (
                f"Hold below {_inr(pw or magnet_raw)} with tape agreement; "
                "stop beyond the last broken put wall, never at the magnet."
            )
            base["kill"] = f"Reclaim above {_inr(pw or magnet_raw)}"

    if sig:
        insight = str(sig.get("insight_text") or _signal_label(sig)).strip().rstrip(".")
        inval = _f(sig.get("invalidation_level"))
        cat = str(sig.get("category") or "").upper()
        theme = base["theme"]
        if cat == "BULLISH":
            theme = "alert-tape-go"
        elif cat == "BEARISH":
            theme = "alert-tape-stop"
        elif cat == "CAUTION":
            theme = "alert-tape-warn"
        drivers = base["drivers"]
        if insight:
            drivers = f"The orderflow tape just printed: {insight}. {drivers}"
        # Mismatch: keep STAY OUT even on a test wall.
        if (
            (base["stance"] == "CALL WATCH" and sig_side == "PUT")
            or (base["stance"] == "PUT WATCH" and sig_side == "CALL")
        ):
            base["stance"] = "STAY OUT"
        kill = base["kill"]
        if inval > 0:
            kill = f"Price gets back through {_inr(inval)}"
        # Keep any WALL CHASE prefix already on the badge; do not strip tokens.
        zone_badge = str(base.get("badge") or "")
        base.update(
            scenario="TAPE_" + str(sig.get("type") or "SIGNAL"),
            badge=f"📡 {_signal_label(sig)} · {zone_badge}",
            theme=theme,
            drivers=drivers,
            kill=kill,
        )

    # Wall-chase badge after tape so the successive-break note stays visible.
    if wall_chase.get("active"):
        side_w = wall_chase.get("side") or ""
        n_ev = int(wall_chase.get("n_events") or 0)
        agrees_chase = (
            (side_w == "CALL" and (sig_side == "CALL" or base.get("stance") == "CALL WATCH"))
            or (side_w == "PUT" and (sig_side == "PUT" or base.get("stance") == "PUT WATCH"))
        )
        if agrees_chase or (sig_side and sig_side == side_w):
            if base.get("stance") == "STAY OUT" and sig_side == side_w:
                base["stance"] = "CALL WATCH" if side_w == "CALL" else "PUT WATCH"
            if "WALL CHASE" not in str(base.get("badge") or ""):
                base["badge"] = f"🏃 WALL CHASE ×{n_ev} · " + str(base.get("badge") or "")
            if "successive-concentration chase" not in str(base.get("drivers") or ""):
                base["drivers"] = (
                    f"Same-side wall break/migrate printed {n_ev}× in the last "
                    f"{int(wall_chase.get('window_s') or 0) // 60}m ({side_w}). "
                    "That is a successive-concentration chase on the map, not a pin. "
                    + str(base.get("drivers") or "")
                )

    # Map-lag / open thrust: quiet note only. Never promote WATCH, never own scenario
    # (scenario ownership would ring the brass desk bell on every flap).
    # If walls chased the *opposite* side of the price burst, demote that WATCH —
    # Sensex 19 Nov open: PUT migrates + ABSORB_BEAR while spot ripped up.
    if map_lag.get("active"):
        side_m = str(map_lag.get("side") or "")
        side_w = str(wall_chase.get("side") or "") if wall_chase.get("active") else ""
        if side_m and side_w and side_m != side_w:
            base["stance"] = "STAY OUT"
            badge = str(base.get("badge") or "")
            if "WALL CHASE" in badge:
                # Drop the opp-side chase token; keep the rest of the badge.
                parts = [p for p in badge.split(" · ") if "WALL CHASE" not in p]
                base["badge"] = " · ".join(parts) if parts else badge
        if base.get("stance") == "STAY OUT":
            move = float(map_lag.get("move_pts") or 0.0)
            why = ", ".join(str(x) for x in (map_lag.get("reasons") or [])[:3])
            if "MAP LAG" not in str(base.get("badge") or ""):
                base["badge"] = f"🗺 MAP LAG · " + str(base.get("badge") or "")
            if "map-lag / open thrust" not in str(base.get("drivers") or "").lower():
                base["drivers"] = (
                    f"Map-lag / open thrust: spot ran {move:+.0f} pts ({side_m}) in ~30m "
                    f"while the wall-chase composite failed ({why or 'walls/tape lag'}). "
                    "Informational only — not a CALL/PUT WATCH. "
                    + str(base.get("drivers") or "")
                )

    stance = base["stance"]
    conf, reasons = _grade_confidence(
        stance=stance, zone=zone, sig=sig, session_closed=False,
    )

    if is_expiry_blast:
        base["badge"] = f"⚡ EXPIRY BLAST {blast_score} · " + str(base.get("badge") or "")
        base["drivers"] = (
            f"It is expiry day, and the blast score is {blast_score} out of 100. "
            "That is a parity watch: it checks whether call and put prices still line up. "
            "It does not pay for a call or a put. "
            + base["drivers"]
        )
    elif is_expiry_pin:
        # Soft map note only — not the blast siren prefix. Risk conditioner still
        # de-sizes from the live card; this line must not sound like expansion.
        base["badge"] = f"📍 EXPIRY PIN {blast_score} · " + str(base.get("badge") or "")
        base["drivers"] = (
            f"Call and put gamma share a strike (pin/compress, score {blast_score}). "
            "That is concentration on the book, not an expansion blast. "
            + base["drivers"]
        )

    return _voice(
        base["scenario"],
        base["badge"],
        base["theme"],
        base["headline"],
        stance=stance,
        confidence=conf,
        where=base["where"],
        drivers=base["drivers"],
        trigger=base["trigger"],
        kill=base["kill"],
        conf_reasons=reasons,
    )
