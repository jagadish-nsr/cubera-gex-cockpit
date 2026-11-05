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

    if pw > 0 and spot <= pw - step:
        return {
            "zone": "BELOW_PUT_WALL",
            "label": "Below put-wall concentration",
            "through_pts": pw - spot,
            "step": step,
        }
    if cw > 0 and spot >= cw + step:
        return {
            "zone": "ABOVE_CALL_WALL",
            "label": "Above call-wall concentration",
            "through_pts": spot - cw,
            "step": step,
        }
    # Two listed strikes leave no interior that is clear of both walls.
    # A one-point wiggle around the midpoint would otherwise flip the headline.
    if cw > pw > 0 and (cw - pw) <= (2 * step) and pw < spot < cw:
        return {
            "zone": "PINNED",
            "label": "Between wall concentrations",
            "through_pts": 0.0,
            "step": step,
        }
    if pw > 0 and abs(spot - pw) <= step:
        return {
            "zone": "TEST_PUT",
            "label": "Testing put-wall concentration",
            "through_pts": abs(spot - pw),
            "step": step,
        }
    if cw > 0 and abs(spot - cw) <= step:
        return {
            "zone": "TEST_CALL",
            "label": "Testing call-wall concentration",
            "through_pts": abs(spot - cw),
            "step": step,
        }
    if pw > 0 and cw > pw and pw < spot < cw:
        return {
            "zone": "PINNED",
            "label": "Between wall concentrations",
            "through_pts": 0.0,
            "step": step,
        }
    return {
        "zone": "UNSITUATED",
        "label": "Walls not a clean corridor",
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
) -> dict | None:
    """Newest HIGH Sentinel print that is still live. Ignores FAILED/REPLACED."""
    best = None
    best_ts = -1.0
    for alert in alerts or []:
        status = str(alert.get("status") or "NEW").upper()
        if status in ("FAILED", "REPLACED"):
            continue
        score = int(alert.get("score") or 0)
        if score < min_score:
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
        reasons.append(
            f"Fresh orderflow print ≥{SIGNAL_MIN_SCORE}: {_signal_label(sig)}."
        )
    else:
        reasons.append(f"No fresh orderflow print ≥{SIGNAL_MIN_SCORE}.")

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
    blast_score = int(blast.get("score") or 0)
    is_expiry_blast = bool(blast.get("is_expiry")) and blast_score >= 60
    breadth = dash.get("market_breadth") or {}
    now_ts = float(now_ts if now_ts is not None else datetime.now(tz=IST).timestamp())

    time_str = _display_clock(str(dash.get("as_of_time") or ""), now_ts)
    snap_as_of = str(dash.get("snapshot_as_of") or "").strip()
    is_pre_bell = bool(dash.get("is_pre_bell_ephemeral"))
    is_morning = (
        str(dash.get("session_phase") or "") == "PRE_MARKET_SETUP"
        or bool(dash.get("is_morning_briefing"))
    )
    is_closed = dash.get("is_market_open") is False or str(dash.get("market_state") or "") == "CLOSED"
    geo = classify_zone(spot, cw, pw, step)
    zone = geo["zone"]
    near_flip = flip > 0 and abs(spot - flip) <= step
    sig = None if (is_pre_bell or is_closed) else pick_fresh_signal(dash.get("orderflow_alerts"), now_ts)
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
        return pack(
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
            where = "This is yesterday's book. Options do not open until 09:15."
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
            trigger="Cash auction at 09:00; opening print near 09:08, then options at 09:15.",
            kill="Any fill before 09:15 is a guess",
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
                "Auction still runs on yesterday's walls; options open at 09:15."
            ),
            drivers=_say(
                f"The auction runs from 09:00 to 09:15. Call wall {_inr(cw)}. Put wall {_inr(pw)}.",
                flip_line,
                f"The opening price prints near 09:08. Price on the screen is {_inr(spot)}.",
                book,
                snap_clause,
            ),
            trigger=(
                "Compare the 09:08 print with these walls, then wait for 09:15."
                if discovered
                else "Wait for the 09:08 print, then options at 09:15."
            ),
            kill="Any fill before 09:15 is a guess",
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

    # Fresh signal on a middle zone: upgrade matching side to WATCH; mismatch stays out.
    if sig and zone in MIDDLE_ZONES:
        if sig_side == "CALL":
            base["stance"] = "CALL WATCH"
            base["trigger"] = f"Hold the bullish tape idea; clear {_inr(cw)} if walls are split."
        elif sig_side == "PUT":
            base["stance"] = "PUT WATCH"
            base["trigger"] = f"Hold the bearish tape idea; clear {_inr(pw)} if walls are split."

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
        base.update(
            scenario="TAPE_" + str(sig.get("type") or "SIGNAL"),
            badge=f"📡 {_signal_label(sig)} · {base['badge'].split(' ', 1)[-1]}",
            theme=theme,
            drivers=drivers,
            kill=kill,
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
