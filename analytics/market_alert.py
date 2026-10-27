"""Marquee tape: geometry-first session read + optional Sentinel overlay.

Walls are OI/gamma concentration under an unvalidated India dealer-sign model.
This module never claims a proven dealer hedge book. Sector breadth is diagnostics
only — not a trend trigger.
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


def _breadth_diag(breadth: dict | None) -> str:
    b = breadth or {}
    adv = int(b.get("advances") or 0)
    dec = int(b.get("declines") or 0)
    ratio = _f(b.get("ratio"), 1.0)
    return f"sectors {adv}/{dec} vs prior close (context, not flow) · ratio {ratio:.2f}x"


def _signal_label(sig: dict) -> str:
    raw = str(sig.get("type") or "SIGNAL").replace("_", " ")
    return raw


def evaluate_market_alert(dash: dict, *, now_ts: float | None = None) -> dict | None:
    """Compose one marquee scenario from cockpit payload fields."""
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
    snap_note = ""
    if snap_as_of and _hms(snap_as_of) != _hms(time_str):
        snap_note = f" · GEX snapshot {snap_as_of}"

    is_pre_bell = bool(dash.get("is_pre_bell_ephemeral"))
    is_morning = (
        str(dash.get("session_phase") or "") == "PRE_MARKET_SETUP"
        or bool(dash.get("is_morning_briefing"))
    )
    is_closed = dash.get("is_market_open") is False or str(dash.get("market_state") or "") == "CLOSED"
    geo = classify_zone(spot, cw, pw, step)
    zone = geo["zone"]
    bdiag = _breadth_diag(breadth)
    near_flip = flip > 0 and abs(spot - flip) <= step
    sig = None if (is_pre_bell or is_closed) else pick_fresh_signal(dash.get("orderflow_alerts"), now_ts)

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
        body = str(pm.get("status_text") or "").strip() or (
            f"Previous NSE close versus call wall {_inr(cw)}, put wall {_inr(pw)}, flip {_inr(flip)}. "
            "This is the last published book."
        )
        return pack(
            scenario="MORNING_BRIEFING",
            badge="🌅 PRIOR-SESSION BOOK",
            theme="alert-tape-spark",
            headline="MORNING BRIEFING — PRIOR-SESSION BOOK",
            meaning=(
                "The saved book is not the last session. The close is withheld."
                if stale_book
                else "Yesterday's walls and flip, before the cash open."
            ),
            body=body,
            action="Cash pre-open is 09:00. Equilibrium prints ~09:08. F&O opens 09:15.",
            diagnostics=f"OI as of {oi} · FII {fii} · prior CVD {cvd_txt} · CW {_inr(cw)} · PW {_inr(pw)} · flip {_inr(flip)}",
        )

    if is_pre_bell:
        discovered = bool((dash.get("pre_market") or {}).get("is_discovered"))
        return pack(
            scenario="PRE_OPEN_SETUP",
            badge="🔔 PRE-OPEN DISCOVERED" if discovered else "🔔 PRE-OPEN SETUP",
            theme="alert-tape-warn",
            headline="PRE-OPEN — PRIOR-SESSION STRUCTURE",
            meaning="Auction is still on yesterday's walls until 09:15.",
            body=(
                f"Cash auction is running (09:00–09:15). Call wall {_inr(cw)} / put wall {_inr(pw)} / "
                f"flip {_inr(flip)} are last published GEX concentration, not live hedging. "
                "are last published gamma concentration. Equilibrium prints ~09:08."
            ),
            action=(
                "Map the 09:08 print vs walls. Do not chase until regular session 09:15."
                if discovered
                else "Hold the prior-session map. Wait for 09:08 equilibrium before treating any gap as real."
            ),
            diagnostics=f"Spot: {_inr(spot)} · CW: {_inr(cw)} · PW: {_inr(pw)}{snap_note}",
        )

    if is_closed:
        return pack(
            scenario="MARKET_CLOSED",
            badge="🌙 SESSION CLOSED",
            theme="alert-tape-spark",
            headline="SESSION CLOSED — EOD SETTLEMENT FROZEN",
            meaning="Last published book. Nothing here is a live hedge.",
            body=(
                "Market session concluded at 15:30 IST. Walls and gamma profiles are frozen at the last "
                "published snapshot. The book is frozen. Nothing is being hedged live."
            ),
            action=f"Review EOD buildup and walls (CW: {_inr(cw)}, PW: {_inr(pw)}). Prepare the next-session plan.",
            diagnostics=f"Spot: {_inr(spot)} · CW: {_inr(cw)} · PW: {_inr(pw)}{snap_note}",
        )

    zone_copy = {
        "BELOW_MAGNET": {
            "scenario": "BELOW_SUPPORT",
            "badge": "📉 BELOW GEX MAGNET",
            "theme": "alert-tape-stop",
            "headline": "BELOW GEX MAGNET — CONCENTRATION BEHIND PRICE",
            "meaning": "Spot is through the only gamma cluster. It is not a pin.",
            "body": (
                f"Spot is {geo['through_pts']:.0f} pts through the single gamma cluster at {_inr(geo.get('magnet') or pw)}. "
                "This is concentration, not a floor."
            ),
            "action": "Do not treat the cluster as a pin. Wait for a reclaim or a fresh Sentinel absorption print.",
        },
        "ABOVE_MAGNET": {
            "scenario": "ABOVE_RESISTANCE",
            "badge": "🔥 ABOVE GEX MAGNET",
            "theme": "alert-tape-spark",
            "headline": "ABOVE GEX MAGNET — CONCENTRATION BEHIND PRICE",
            "meaning": "Spot is through the only gamma cluster. It is not a squeeze.",
            "body": (
                f"Spot is {geo['through_pts']:.0f} pts through the single gamma cluster at {_inr(geo.get('magnet') or cw)}. "
                "A break of the cluster is a location, not a squeeze."
            ),
            "action": "Do not treat the cluster as intact. Wait until spot is back inside one strike step.",
        },
        "TEST_MAGNET": {
            "scenario": "TEST_MAGNET",
            "badge": "⚖️ AT GEX MAGNET",
            "theme": "alert-tape-warn",
            "headline": "AT GEX MAGNET — NO WALL CORRIDOR",
            "meaning": "Call and put concentration share one strike. There is no corridor.",
            "body": (
                f"Call and put concentration sit on the same strike {_inr(geo.get('magnet') or cw)}. "
                "There is no corridor between two strikes."
            ),
            "action": "Stand aside on fade templates until the call and put strikes split. Use Sentinel for tape.",
        },
        "BELOW_PUT_WALL": {
            "scenario": "BELOW_SUPPORT",
            "badge": "📉 PUT WALL BREACHED",
            "theme": "alert-tape-stop",
            "headline": "BELOW PUT-WALL CONCENTRATION",
            "meaning": "The put-gamma strike is already behind price.",
            "body": (
                f"Spot is {geo['through_pts']:.0f} pts through the put-gamma strike {_inr(pw)}. "
                "The strike is already behind price. It is not a floor."
            ),
            "action": "Do not buy dips as if the strike is holding. Look for a reclaim or a confirmed absorption print.",
        },
        "ABOVE_CALL_WALL": {
            "scenario": "ABOVE_RESISTANCE",
            "badge": "🔥 CALL WALL BREACHED",
            "theme": "alert-tape-spark",
            "headline": "ABOVE CALL-WALL CONCENTRATION",
            "meaning": "The call-gamma strike is already behind price.",
            "body": (
                f"Spot is {geo['through_pts']:.0f} pts through the call-gamma strike {_inr(cw)}. "
                "The strike is already behind price. It is not trapped-seller proof."
            ),
            "action": "Do not treat the strike as intact. Wait for a close back inside the cluster or Sentinel exhaustion.",
        },
        "TEST_PUT": {
            "scenario": "TEST_PUT",
            "badge": "🚨 PUT WALL TEST",
            "theme": "alert-tape-stop",
            "headline": "TESTING PUT-WALL CONCENTRATION",
            "meaning": "Within one strike step of the put wall. A touch is not a floor.",
            "body": (
                f"Spot is within one strike step of the put-gamma strike {_inr(pw)}. "
                "Watch absorption versus a sweep. A touch is not a floor."
            ),
            "action": "Do not fade the strike. Cut the idea if price prints through by more than one step.",
        },
        "TEST_CALL": {
            "scenario": "TEST_CALL",
            "badge": "🔥 CALL WALL TEST",
            "theme": "alert-tape-spark",
            "headline": "TESTING CALL-WALL CONCENTRATION",
            "meaning": "Within one strike step of the call wall. A touch is not a squeeze.",
            "body": (
                f"Spot is within one strike step of the call-gamma strike {_inr(cw)}. "
                "Watch absorption versus continuation. A touch is not a squeeze."
            ),
            "action": "Do not assume a squeeze on a touch. Need a 5-min hold through the strike plus tape confirmation.",
        },
        "PINNED": {
            "scenario": "SIDEWAYS_CHOP",
            "badge": "⚖️ RANGE BETWEEN WALLS",
            "theme": "alert-tape-warn",
            "headline": "BETWEEN WALL CONCENTRATIONS",
            "meaning": "Spot is inside the corridor. The corridor is a map, not a fade.",
            "body": (
                f"Spot is inside the corridor {_inr(pw)}–{_inr(cw)}. "
                "The corridor is a map. It is not a reason to fade either strike."
            ),
            "action": "Do not fade the edges because price is between the strikes. Use Sentinel tape for direction.",
        },
        "UNSITUATED": {
            "scenario": "UNSITUATED",
            "badge": "⚖️ STRUCTURE UNCLEAR",
            "theme": "alert-tape-warn",
            "headline": "WALL GEOMETRY UNCLEAR",
            "meaning": "The strikes do not form a clean corridor around spot.",
            "body": "Call/put concentrations do not form a clean corridor around spot. No pin or breach claim.",
            "action": "Stand aside on wall templates. Use Sentinel tape and wait for structure.",
        },
        "UNKNOWN": {
            "scenario": "UNSITUATED",
            "badge": "⚖️ NO SPOT",
            "theme": "alert-tape-warn",
            "headline": "NO LIVE SPOT",
            "meaning": "The tape cannot place price against the walls.",
            "body": "Tape cannot locate price vs walls.",
            "action": "Wait for a live quote.",
        },
    }
    base = dict(zone_copy.get(zone) or zone_copy["UNSITUATED"])

    if sig:
        insight = str(sig.get("insight_text") or _signal_label(sig))
        play = str(sig.get("actionable_play") or "").strip()
        inval = _f(sig.get("invalidation_level"))
        cat = str(sig.get("category") or "").upper()
        theme = base["theme"]
        if cat == "BULLISH":
            theme = "alert-tape-go"
        elif cat == "BEARISH":
            theme = "alert-tape-stop"
        elif cat == "CAUTION":
            theme = "alert-tape-warn"
        action = play or base["action"]
        if inval > 0 and "invalidat" not in action.lower():
            action = f"{action} Invalidation {_inr(inval)}."
        base.update(
            scenario="TAPE_" + str(sig.get("type") or "SIGNAL"),
            badge=f"📡 {_signal_label(sig)} · {base['badge'].split(' ', 1)[-1]}",
            theme=theme,
            headline=f"{_signal_label(sig)} · {base['headline']}",
            body=f"{insight} Geometry: {base['body']}",
            action=action,
        )

    if is_expiry_blast:
        base["badge"] = f"⚡ 0DTE BLAST {blast_score} · " + str(base.get("badge") or "")
        base["body"] = (
            f"Expiry gamma-blast score {blast_score}/100 (parity crossover watch, not dealer sign). "
            + base["body"]
        )

    flip_bit = f" · Flip {_inr(flip)}" if flip > 0 else ""
    if near_flip:
        flip_bit += " · within 1 step of flip (writer-imbalance map, unvalidated)"
    blast_bit = f" · Blast {blast_score}/100"
    if blast.get("is_expiry"):
        blast_bit += " 0DTE"
    sig_bit = ""
    if sig:
        sig_bit = f" · {_signal_label(sig)} {int(sig.get('score') or 0)}/{max(1, int(sig.get('_age_s') or 0)):.0f}s"

    return pack(
        scenario=base["scenario"],
        badge=base["badge"],
        theme=base["theme"],
        headline=base["headline"],
        meaning=base.get("meaning") or "",
        body=base["body"],
        action=base["action"],
        diagnostics=(
            f"Spot {_inr(spot)} · CW {_inr(cw)} · PW {_inr(pw)}{flip_bit}{blast_bit}{sig_bit} · {bdiag} · {regime}"
        ),
    )
