"""Alert tape: geometry-first session read + optional Sentinel overlay.

Walls are OI/gamma concentration under an unvalidated India dealer-sign model.
This module never claims a proven dealer hedge book. Sector breadth is diagnostics
only — not a trend trigger. Each state is one plain sentence, the levels, and
one next step.
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
    sit = (
        "The index can still rise, fall, or simply sit. "
        "Sitting is the quiet loss, because the premium ticks away while you wait."
    )

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

    def _voice(scenario, badge, theme, headline, outcome, drivers, risk):
        return pack(
            scenario=scenario,
            badge=badge,
            theme=theme,
            headline=headline,
            meaning="",
            outcome=outcome,
            drivers=drivers,
            risk=risk,
            body=outcome,
            action=risk,
            diagnostics=drivers,
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
            outcome = (
                "Do not buy a call or a put off this snapshot. The saved book is not yesterday's close, "
                "so the close number is withheld. Premium paid on a withheld close is a guess."
            )
        else:
            outcome = (
                "Do not buy a call or a put yet. This is yesterday's book, and options do not open until 09:15. "
                "You would be paying premium on a map that can change at the bell."
            )
        return _voice(
            "MORNING_BRIEFING",
            "🌅 YESTERDAY'S BOOK",
            "alert-tape-spark",
            "Before the open",
            outcome,
            _say(brief, f"Call wall {_inr(cw)}. Put wall {_inr(pw)}.", flip_line, book, *extras),
            "Cash auction starts at 09:00 and the opening price prints near 09:08. "
            "Wait for that print before you treat a gap as real. A gap can reverse, "
            "and the option still needs a move large enough to cover what you paid.",
        )

    if is_pre_bell:
        discovered = bool((dash.get("pre_market") or {}).get("is_discovered"))
        return _voice(
            "PRE_OPEN_SETUP",
            "🔔 OPENING PRICE IN" if discovered else "🔔 PRE-OPEN",
            "alert-tape-warn",
            "Pre-open auction",
            "Stay out of a call and stay out of a put until 09:15. The auction is still running on yesterday's walls, "
            "and a fill now is premium paid on a map the open can rewrite.",
            _say(
                f"The auction runs from 09:00 to 09:15. Call wall {_inr(cw)}. Put wall {_inr(pw)}.",
                flip_line,
                f"The opening price prints near 09:08. Price on the screen is {_inr(spot)}.",
                book,
                snap_clause,
            ),
            "Compare the 09:08 price with these walls, and still wait for options to open at 09:15."
            if discovered
            else "Wait for the 09:08 print before you treat a gap as real. The option you buy still needs a move large enough to cover the premium.",
        )

    if is_closed:
        return _voice(
            "MARKET_CLOSED",
            "🌙 SESSION CLOSED",
            "alert-tape-spark",
            "Session closed",
            "Do not buy a new option off this screen. Cash closed at 15:30, and this is the last snapshot. "
            "A long option held overnight can gap against you while the premium keeps decaying.",
            _say(
                f"Price is {_inr(spot)}. Call wall {_inr(cw)}. Put wall {_inr(pw)}.",
                flip_line,
                book,
                snap_clause,
            ),
            "Use these strikes to plan the next session. A new buy waits for the next open.",
        )

    pts = f"{geo['through_pts']:.0f}"
    magnet = _inr(geo.get("magnet") or cw or pw)
    zone_copy = {
        "BELOW_MAGNET": {
            "scenario": "BELOW_SUPPORT",
            "badge": "📉 BELOW THE CLUSTER",
            "theme": "alert-tape-stop",
            "headline": "Price is under the only big gamma strike",
            "outcome": (
                f"Stay out of a call. The only big gamma strike is up at {magnet}, {pts} points above price. "
                "A call needs the index to climb back there, and this print does not give you that climb. "
                "If the fall continues, the call can end up worth nothing."
            ),
            "drivers": _say(
                "Calls and puts are piled on that one strike.",
                book, flip_line, breadth_line, sit,
            ),
            "risk": (
                f"If you already hold a call, exit the idea while price stays below {magnet}. "
                "Wait until price is back above that strike before you pay for a rise."
            ),
        },
        "ABOVE_MAGNET": {
            "scenario": "ABOVE_RESISTANCE",
            "badge": "🔥 ABOVE THE CLUSTER",
            "theme": "alert-tape-spark",
            "headline": "Price is over the only big gamma strike",
            "outcome": (
                f"Stay out of a put that needs price to fall back to {magnet}. "
                f"Price is {pts} points above that only big gamma strike. "
                "A put bought here still needs a fall large enough to cover the premium."
            ),
            "drivers": _say("Calls and puts share that one strike.", book, flip_line, breadth_line, sit),
            "risk": (
                f"A call bought above {magnet} still needs a further rise to pay for itself. "
                "Treat the strike as back in play only when price is within one strike of it again."
            ),
        },
        "TEST_MAGNET": {
            "scenario": "TEST_MAGNET",
            "badge": "⚖️ ONE STRIKE",
            "theme": "alert-tape-warn",
            "headline": "Call and put gamma share one strike",
            "outcome": (
                f"Stay out of a call and stay out of a put. Call gamma and put gamma sit on one strike, {magnet}, "
                "so there is no range to trade. While you wait for the strikes to split, the premium ticks away."
            ),
            "drivers": _say(book, flip_line, breadth_line, sit),
            "risk": "Wait until the call strike and the put strike separate. Until then you are paying to wait.",
        },
        "BELOW_PUT_WALL": {
            "scenario": "BELOW_SUPPORT",
            "badge": "📉 UNDER PUT WALL",
            "theme": "alert-tape-stop",
            "headline": "Put wall is behind price",
            "outcome": (
                f"Do not buy a call as a dip. The put wall at {_inr(pw)} is already {pts} points above price, "
                "so it is not holding the market up. If the fall continues, the call premium can go to zero."
            ),
            "drivers": _say(f"The call wall is {_inr(cw)}.", flip_line, book, breadth_line, sit),
            "risk": (
                f"A call you already hold is on the wrong side of {_inr(pw)}. "
                "A new put is late: the break you would be buying has already happened, and you still pay full premium."
            ),
        },
        "ABOVE_CALL_WALL": {
            "scenario": "ABOVE_RESISTANCE",
            "badge": "🔥 OVER CALL WALL",
            "theme": "alert-tape-spark",
            "headline": "Call wall is behind price",
            "outcome": (
                f"Do not buy a put expecting price to fall back to {_inr(cw)}. "
                f"Price is {pts} points above that call wall, so the strike is already behind you."
            ),
            "drivers": _say(f"The put wall is {_inr(pw)}.", flip_line, book, breadth_line),
            "risk": (
                f"A put here needs a fall back through {_inr(cw)}, or the premium decays while you wait. "
                "A call bought after the break still needs a further rise big enough to pay for that premium."
            ),
        },
        "TEST_PUT": {
            "scenario": "TEST_PUT",
            "badge": "🚨 TESTING PUT WALL",
            "theme": "alert-tape-stop",
            "headline": "Price is at the put wall",
            "outcome": (
                f"Stay out of a call. Price has reached the put wall at {_inr(pw)}. "
                "You pay the premium up front for a rise, and reaching that strike does not give you the rise. "
                "If the fall continues, the call can end up worth nothing."
            ),
            "drivers": _say(
                f"Price is within one strike of {_inr(pw)}. The call wall is {_inr(cw)}.",
                flip_line, book, breadth_line, sit,
            ),
            "risk": (
                f"If you already hold a call, exit it when price goes more than one strike below {_inr(pw)}. "
                "Buying a put only because price reached the strike still needs the index to fall far enough to cover what you paid."
            ),
        },
        "TEST_CALL": {
            "scenario": "TEST_CALL",
            "badge": "🔥 TESTING CALL WALL",
            "theme": "alert-tape-spark",
            "headline": "Price is at the call wall",
            "outcome": (
                f"Stay out of a call for now. Price has reached the call wall at {_inr(cw)}. "
                "You would be paying for a further rise, and reaching the strike does not give you that rise. "
                "If price turns down, the premium is the loss."
            ),
            "drivers": _say(
                f"Price is within one strike of {_inr(cw)}. The put wall is {_inr(pw)}.",
                flip_line, book, breadth_line,
            ),
            "risk": (
                f"A call only stays in play after price holds above {_inr(cw)} for 5 minutes and the orderflow tape agrees. "
                "If it fails there, exit. A put bought into that high still needs the index to fall back through the strike "
                "far enough to cover what you paid."
            ),
        },
        "PINNED": {
            "scenario": "SIDEWAYS_CHOP",
            "badge": "⚖️ BETWEEN WALLS",
            "theme": "alert-tape-warn",
            "headline": "Price is between the walls",
            "outcome": (
                f"Do not buy a call near {_inr(cw)} or a put near {_inr(pw)} just because price is in the middle. "
                "While it stays between the walls, the premium you paid ticks away."
            ),
            "drivers": _say(
                f"Price sits between the put wall at {_inr(pw)} and the call wall at {_inr(cw)}.",
                flip_line, book, breadth_line, sit,
            ),
            "risk": (
                f"A call needs price to clear {_inr(cw)}. A put needs price to clear {_inr(pw)}. "
                "Until one of those happens, you are paying to wait."
            ),
        },
        "UNSITUATED": {
            "scenario": "UNSITUATED",
            "badge": "⚖️ WALLS UNCLEAR",
            "theme": "alert-tape-warn",
            "headline": "No clean range around price",
            "outcome": (
                "Stay out of a call and stay out of a put that depend on a wall. "
                "The call strike and the put strike do not form a range around price, "
                "so there is no level here that pays for the premium."
            ),
            "drivers": _say(
                f"Price is {_inr(spot)}. Call wall {_inr(cw)}. Put wall {_inr(pw)}.",
                flip_line, book, breadth_line,
            ),
            "risk": (
                "Wait until the strikes line up. Until then the orderflow tape is the only read, "
                "and a premium paid on an unclear map can go to zero while you wait."
            ),
        },
        "UNKNOWN": {
            "scenario": "UNSITUATED",
            "badge": "⚖️ NO PRICE",
            "theme": "alert-tape-warn",
            "headline": "Waiting for a live quote",
            "outcome": "Wait for a live price. There is nothing to buy until the tape can place the index against the walls.",
            "drivers": "The walls may be on the screen, but price is missing.",
            "risk": "A premium paid without a live price is a guess.",
        },
    }
    base = dict(zone_copy.get(zone) or zone_copy["UNSITUATED"])

    if sig:
        insight = str(sig.get("insight_text") or _signal_label(sig)).strip().rstrip(".")
        play = str(sig.get("actionable_play") or "").strip().rstrip(".")
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
        risk = base["risk"]
        if inval > 0:
            risk = f"That tape idea is finished if price gets back through {_inr(inval)}. {risk}"
        if play and play.lower() not in risk.lower():
            risk = f"{risk} {play}."
        base.update(
            scenario="TAPE_" + str(sig.get("type") or "SIGNAL"),
            badge=f"📡 {_signal_label(sig)} · {base['badge'].split(' ', 1)[-1]}",
            theme=theme,
            drivers=drivers,
            risk=risk,
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
        base["outcome"],
        base["drivers"],
        base["risk"],
    )
