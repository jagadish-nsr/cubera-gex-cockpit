"""Gamma-blast helpers — pure functions for the expiry-day crossover watch.

The signal (decoded via put-call parity): at a FIXED strike K near expiry,
    call_premium - put_premium = DF*(F - K) ≈ spot - K,
so the ATM call and put premiums CROSS exactly when spot crosses K. We anchor two
fixed strikes (the magnet and the 1:45pm ATM), watch their call vs put, and flag
the crossover = spot crossing that level (a breakout pivot; near expiry the high
gamma can amplify the continuation — a 'gamma blast').

Everything here is pure + sign-free (parity only — no dealer-sign assumption). It
LOCATES candidate strikes and detects crossings; it does NOT claim pin-vs-blast
direction (that depends on the UNVALIDATED India dealer sign — see CLAUDE.md).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os

# |call - put| within this many points counts as "at the crossover" (spot ≈ K).
CROSS_TOL_PTS = 5.0

# The 0-DTE "blast window" starts here — the afternoon cut the retail gamma-blast play lives in.
# Studies historically froze it at 13:45 (the 1:45pm ATM anchor). Overridable via the env var
# BLAST_AFT_CUT='HH:MM' so the whole study suite can be re-run at a different freeze (e.g. 14:30)
# from one place, without editing each script's default. Returns (minutes_since_midnight, label).
BLAST_AFT_CUT_DEFAULT = "13:45"


def afternoon_cut():
    """(minutes_since_midnight, 'HH:MM') for the blast-window start; env BLAST_AFT_CUT overrides."""
    s = os.environ.get("BLAST_AFT_CUT", BLAST_AFT_CUT_DEFAULT).strip()
    h, m = s.split(":")
    return int(h) * 60 + int(m), f"{int(h):02d}:{int(m):02d}"


def atm_strike(strikes, spot: float):
    """The listed strike nearest to spot (the ATM strike), or None if empty."""
    ks = [float(k) for k in strikes if k is not None]
    return min(ks, key=lambda k: abs(k - spot)) if ks else None


def _snap(strikes, target):
    """Snap a (possibly off-grid) level to the nearest listed strike."""
    return atm_strike(strikes, target) if target is not None else None


def pick_anchors(oi_strikes, spot: float, magnet: float) -> dict:
    """Two FIXED strikes to watch: the magnet (peak-GEX pin) and the ATM strike,
    each snapped to a listed strike. Returns {"magnet": K|None, "atm": K|None}."""
    strikes = [s.get("strike") for s in (oi_strikes or [])]
    return {"magnet": _snap(strikes, magnet), "atm": atm_strike(strikes, spot)}


def strike_premiums(oi_strikes, strike) -> tuple:
    """(call_ltp, put_ltp) for a strike from the per-strike list, or (None, None)."""
    if strike is None:
        return (None, None)
    for s in oi_strikes or []:
        if s.get("strike") == strike:
            return (s.get("call_ltp"), s.get("put_ltp"))
    return (None, None)


def strike_oi(oi_strikes, strike) -> tuple:
    """(call_oi, put_oi) for a strike from the per-strike list, or (None, None)."""
    if strike is None:
        return (None, None)
    for s in oi_strikes or []:
        if s.get("strike") == strike:
            return (s.get("call_oi"), s.get("put_oi"))
    return (None, None)


def next_strike(oi_strikes, strike, direction: str):
    """The next listed strike in the travel direction ('up' → above, 'down' → below)
    the crossed ``strike`` — the wall that can cap a continuation. None if none."""
    if strike is None:
        return None
    ks = sorted(float(s["strike"]) for s in (oi_strikes or []) if s.get("strike") is not None)
    above = [k for k in ks if k > strike]
    below = [k for k in ks if k < strike]
    return (above[0] if above else None) if direction == "up" else (below[-1] if below else None)


def crossover_state(call, put, tol: float = CROSS_TOL_PTS) -> dict | None:
    """Where is spot relative to the anchored strike, read off the premiums?

    Returns {"diff": call-put, "side": "call_rich"|"put_rich"|"at_cross",
    "label": <plain text>} or None if either premium is missing. By parity
    diff ≈ spot - K, so call_rich => spot above K, put_rich => spot below K.
    """
    if call is None or put is None:
        return None
    diff = float(call) - float(put)
    if abs(diff) <= tol:
        side, label = "at_cross", "AT crossover — spot ≈ strike (pivot)"
    elif diff > 0:
        side, label = "call_rich", "spot ABOVE strike (call richer)"
    else:
        side, label = "put_rich", "spot BELOW strike (put richer)"
    return {"diff": diff, "side": side, "label": label}


def detect_crossovers(series: list[dict], tol: float = CROSS_TOL_PTS) -> list[dict]:
    """Find crossover events in a time-ordered premium series for one anchored
    strike. ``series`` items: {"time_ist": str, "call": float, "put": float}.

    A crossover is a SIGN CHANGE of (call - put) between consecutive points (spot
    passing through the strike). Returns [{"time_ist", "from", "to"}], where
    from/to are "call_rich"/"put_rich" sides.
    """
    pts = [(p["time_ist"], float(p["call"]) - float(p["put"]))
           for p in series if p.get("call") is not None and p.get("put") is not None]
    events: list[dict] = []
    prev_side = None
    for t, diff in pts:
        side = "call_rich" if diff > 0 else "put_rich" if diff < 0 else None
        if side is None:
            continue
        if prev_side is not None and side != prev_side:
            events.append({"time_ist": t, "from": prev_side, "to": side})
        prev_side = side
    return events


def detect_crossovers_spot(series: list[dict], strike: float) -> list[dict]:
    """The SAME crossover, timed off SPOT instead of the premium pair.

    Put-call parity: ``call − put = S − K·e^(−rT)``, so the sign change of (call − put) *is* spot
    crossing the (discounted) strike. On expiry day T≈0, so S vs K is exact to within carry — and
    this is an expiry-day feature. Sign-free, no dealer-sign assumption, same as the premium form.

    Why it exists: the premium series moves at the OPTION-CHAIN cadence (1-5 min), so a cross could
    only ever be timed to ±one chain pull — which blurs the §7d "Fast TRUE within 5 min of the
    cross" classification that the whole read hangs on. Spot has its own ~10s lane, so feeding it
    here times the cross an order of magnitude more precisely for free.

    ``series`` items: {"time_ist": str, "spot": float}. Returns the same shape as
    ``detect_crossovers`` — [{"time_ist", "from", "to"}] with "call_rich" (spot above K) /
    "put_rich" (spot below K), so callers are unchanged.
    """
    pts = [(p["time_ist"], float(p["spot"]) - float(strike))
           for p in series if p.get("spot") is not None]
    events: list[dict] = []
    prev_side = None
    for t, diff in pts:
        side = "call_rich" if diff > 0 else "put_rich" if diff < 0 else None
        if side is None:
            continue
        if prev_side is not None and side != prev_side:
            events.append({"time_ist": t, "from": prev_side, "to": side})
        prev_side = side
    return events


# --- True-vs-fake blast scoring -------------------------------------------------
# As spot crosses a fixed strike K, the option side that goes ITM is the writers'
# pain side (calls when spot rises through K, puts when spot falls through K). A
# SUSTAINING ("true") break shows those writers COVERING (that side's OI falling)
# and its premium EXPANDING — the wall is capitulating, which fuels continuation. A
# FAKE break shows writers DEFENDING (OI flat/rising) and/or a big INTACT opposite
# wall one strike ahead absorbing the move → spot reverts to the pin. All sign-free
# (raw OI + premium; no dealer-sign assumption). Heuristic, decision-support only.
_ITM_SIDE = {"up": "call", "down": "put"}   # side going ITM AND the continuation-wall side ahead

# Calibration scales (fraction at which a component saturates), set from the two
# captured expiry events: 07-02 up-cross of 24150 (cover −0.12, prem +0.21, intact
# wall ahead → WEAK/capped, ran +42 then faded) vs 07-03 down-cross of 24350 (cover
# −0.36, prem +0.18, wall ahead eroding → TRUE, ran ~90). Tiny sample — see docs.
_FULL_COVER = 0.30      # ITM-side OI shrinking 30% over the window = full ignition
_FULL_THRUST = 0.20     # ITM-side premium expanding 20% = full thrust confirmation


def _clip(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def grade_blast(cover_frac: float, prem_exp: float | None,
                wall_ratio: float | None = None, wall_frac: float | None = None,
                full_cover: float = _FULL_COVER, full_thrust: float = _FULL_THRUST) -> dict:
    """Score + grade a crossing from its COMPONENT values (the single grading source,
    shared by the live ``blast_score`` and offline dataset analysis so they can never
    drift). ``cover_frac`` <0 = ITM-side writers covering (fuel), >+0.05 = defending;
    ``prem_exp`` = ITM-side premium expansion; ``wall_ratio``/``wall_frac`` describe the
    wall one strike ahead (None ⇒ clear path). Returns {score, grade, ignition, thrust,
    path} — same 0.5/0.2/0.3 weighting and TRUE/WEAK/FAKE thresholds as before."""
    ignition = _clip(-cover_frac / full_cover)
    thrust = _clip((prem_exp or 0.0) / full_thrust)
    path = 1.0                                    # clear path unless a wall says otherwise
    if wall_ratio is not None:
        intact = _clip(wall_ratio / 3.0) * (1.0 if (wall_frac or 0.0) > -0.15 else 0.3)
        path = 1.0 - intact
    score = round(100 * (0.5 * ignition + 0.2 * thrust + 0.3 * path))
    if cover_frac > 0.05:
        grade = "FAKE-leaning (writers defending K)"
    elif score >= 60:
        grade = "TRUE-leaning (break sustaining)"
    elif score >= 35:
        grade = "WEAK / unconfirmed"
    else:
        grade = "FAKE-leaning (pin/cap)"
    return {"score": score, "grade": grade, "ignition": ignition, "thrust": thrust, "path": path}


def _seq_ends(seq, key):
    """First and last non-null value of ``key`` across a time-ordered list of dicts."""
    vals = [s.get(key) for s in seq
            if s.get(key) is not None and s.get(key) == s.get(key)]
    return (float(vals[0]), float(vals[-1])) if vals else (None, None)


def blast_score(direction: str, crossed: list[dict], ahead: list[dict] | None = None,
                full_cover: float = _FULL_COVER, full_thrust: float = _FULL_THRUST) -> dict | None:
    """Grade a strike-crossing as a TRUE (sustaining) vs FAKE (pin/cap) blast.

    ``direction`` is 'up'/'down' (how spot crossed the strike). ``crossed`` is the
    time-ordered snapshot list for the crossed strike (dicts with ``call_oi``,
    ``put_oi``, ``call_ltp``, ``put_ltp``), spanning from ~the crossing forward.
    ``ahead`` is the same for the NEXT listed strike in the travel direction (its
    same-side OI is the wall that can cap the move); pass a single-snapshot list (or
    None) when only the current wall is known — then only its size, not its drift,
    is used. Returns component fractions + a 0–100 sustain ``score`` and a ``grade``:

      ignition = ITM-side writers covering  (−cover_frac, saturating at full_cover)
      thrust   = ITM-side premium expansion (saturating at full_thrust)
      path     = 1 − wall-ahead intactness  (big, non-eroding wall ahead ⇒ cap)
      score    = 100·(0.5·ignition + 0.2·thrust + 0.3·path)

    Writers ADDING OI at K (cover_frac > +0.05) forces a FAKE grade (active defence).
    Sign-free, heuristic; None if inputs are unusable.
    """
    if direction not in _ITM_SIDE or not crossed:
        return None
    side = _ITM_SIDE[direction]
    oi0, oi1 = _seq_ends(crossed, f"{side}_oi")
    p0, p1 = _seq_ends(crossed, f"{side}_ltp")
    if not oi0:                                   # need a non-zero starting OI
        return None
    cover_frac = (oi1 - oi0) / oi0                # <0 = covering (fuel); >0 = defending
    prem_exp = (p1 / p0 - 1.0) if (p0 and p1) else None

    wall_ratio = wall_frac = None
    if ahead:
        aoi0, aoi1 = _seq_ends(ahead, f"{side}_oi")
        if aoi1 and oi1:
            wall_ratio = aoi1 / oi1               # wall size relative to the crossed strike
            wall_frac = (aoi1 - aoi0) / aoi0 if aoi0 else 0.0

    g = grade_blast(cover_frac, prem_exp, wall_ratio, wall_frac, full_cover, full_thrust)
    return {"direction": direction, "itm_side": side, **g,
            "cover_frac": cover_frac, "prem_exp": prem_exp,
            "wall_ratio": wall_ratio, "wall_frac": wall_frac}


def gamma_concentration(oi_strikes, spot: float, n: int = 6,
                        band_pts: float = 600.0) -> list[dict]:
    """Candidate gamma-blast strikes: those near spot with the largest gamma
    EXPOSURE (|net_gex|), each with its fresh OI build (ΔOI). High |net_gex| +
    growing OI near spot = where the energy is concentrated. Sign-free (it locates
    energy; whether it pins or blasts is the unvalidated dealer-sign question).

    Returns up to ``n`` dicts {strike, net_gex, call_doi, put_doi, dist} sorted by
    |net_gex| desc, restricted to |strike - spot| <= band_pts.
    """
    near = [s for s in (oi_strikes or [])
            if s.get("net_gex") is not None and abs(s["strike"] - spot) <= band_pts]
    near.sort(key=lambda s: abs(float(s["net_gex"])), reverse=True)
    out = []
    for s in near[:n]:
        out.append({
            "strike": s["strike"], "net_gex": float(s["net_gex"]),
            "call_doi": s.get("call_doi"), "put_doi": s.get("put_doi"),
            "dist": s["strike"] - spot,
        })
    return out


def evaluate_dashboard_gamma_blast(dash, symbol: str = "NIFTY") -> dict:
    """Evaluate point-in-time Expiry Gamma Blast status and 0-100 score for Cockpit.

    Pure, robust function that evaluates:
      1. Expiry Status (0-DTE vs non-expiry session).
      2. Session Window (Morning Pin vs 13:00+ Afternoon Blast Window).
      3. Wall Proximity & Breaches (Call Wall short squeeze vs Put Wall trapdoor breakdown).
      4. Writer Ignition & Defense (OI covering vs building from oi_strikes).
      5. GEX / Regime Confluence (Spot < Flip negative accelerator zone).

    Returns a dict with:
      - state: 'TRIGGERED' | 'COILING' | 'NO_TRIGGER'
      - badge: 'GAMMA BLAST: ACTIVE' | 'GAMMA BLAST: COILING' | 'GAMMA BLAST: INACTIVE'
      - description: Plain text diagnostic summary.
      - score: int (0 to 100)
      - is_expiry: bool
      - in_blast_window: bool
      - primary_strike: float | None
    """
    levels = getattr(dash, "levels", None)
    spot = float(getattr(levels, "spot", 0.0) or 0.0)
    if spot <= 0:
        return {
            "state": "NO_TRIGGER",
            "badge": "GAMMA BLAST: INACTIVE",
            "description": "No spot on the book. The gamma-blast watch has nothing to mark.",
            "score": None,
            "is_expiry": False,
            "in_blast_window": False,
            "primary_strike": None,
        }

    call_wall = float(getattr(levels, "call_wall", 0.0) or 0.0)
    put_wall = float(getattr(levels, "put_wall", 0.0) or 0.0)
    flip = float(getattr(levels, "flip", 0.0) or 0.0)
    magnet = float(getattr(levels, "magnet", 0.0) or 0.0)
    net_gex = float(getattr(levels, "total_net_gex", 0.0) or 0.0)
    regime = str(getattr(getattr(dash, "view", None), "regime", "NEUTRAL") or "NEUTRAL").upper()
    oi_strikes = getattr(dash, "oi_strikes", None) or []

    # 1. IST Datetime & Session Window
    fetched_at = getattr(dash, "fetched_at", None)
    if fetched_at:
        if getattr(fetched_at, "tzinfo", None) is None:
            dt_ist = fetched_at + timedelta(hours=5, minutes=30)
        else:
            dt_ist = fetched_at.astimezone(timezone(timedelta(hours=5, minutes=30)))
    else:
        dt_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))

    ist_min = dt_ist.hour * 60 + dt_ist.minute
    cut_min, _ = afternoon_cut()
    # Window opens around 13:00 IST and peaks from 13:45 to 15:30 IST
    in_blast_window = (ist_min >= 13 * 60 and ist_min <= 15 * 60 + 30)

    # Off-market hours safety gate: dealer gamma blast model is strictly an active market regime
    import config
    if not config.is_market_open(dt_ist) or ist_min > 15 * 60 + 30 or ist_min < 9 * 60 + 15:
        return {
            "state": "NO_TRIGGER",
            "badge": "GAMMA BLAST: SESSION CONCLUDED",
            "description": "Market session closed at 15:30 IST. The gamma-blast watch is off.",
            "score": 0,
            "is_expiry": False,
            "in_blast_window": False,
            "primary_strike": None,
        }

    # 2. 0-DTE Expiry Gate
    dte_now = getattr(dash, "dte_now", None)
    expiries = getattr(dash, "expiries", []) or []
    is_expiry = False
    if dte_now is not None:
        is_expiry = float(dte_now) < 0.6
    elif expiries:
        first_exp = expiries[0]
        if hasattr(first_exp, "strftime"):
            is_expiry = (first_exp.strftime("%Y-%m-%d") == dt_ist.strftime("%Y-%m-%d"))
        elif isinstance(first_exp, str):
            is_expiry = first_exp.startswith(dt_ist.strftime("%Y-%m-%d"))
    else:
        try:
            from data.session_flags import is_expiry_day
            is_expiry = is_expiry_day(symbol, dt_ist.strftime("%Y-%m-%d"))
        except Exception:
            pass

    step = 50.0 if symbol.upper() == "NIFTY" else 100.0

    # 3. Wall Proximity & Target Strike
    cw_dist = call_wall - spot if call_wall > 0 else 9999.0
    pw_dist = spot - put_wall if put_wall > 0 else 9999.0

    cw_breach = (call_wall > 0 and spot >= call_wall - 5.0)
    cw_testing = (call_wall > 0 and spot >= call_wall - step * 0.6)
    pw_breach = (put_wall > 0 and spot <= put_wall + 5.0)
    pw_testing = (put_wall > 0 and spot <= put_wall + step * 0.6)

    # 4. Strike OI & Covering Ratio (Ignition)
    cover_frac = 0.0
    primary_strike = None
    if cw_testing or (cw_dist <= pw_dist and call_wall > 0):
        primary_strike = call_wall
        for s in oi_strikes:
            if abs(float(s.get("strike", 0.0)) - call_wall) < step / 2:
                coi = float(s.get("call_oi") or 0.0)
                c_doi = float(s.get("call_doi") or s.get("call_doi_near") or 0.0)
                if coi > 0:
                    cover_frac = c_doi / coi
                break
    elif pw_testing or put_wall > 0:
        primary_strike = put_wall
        for s in oi_strikes:
            if abs(float(s.get("strike", 0.0)) - put_wall) < step / 2:
                poi = float(s.get("put_oi") or 0.0)
                p_doi = float(s.get("put_doi") or s.get("put_doi_near") or 0.0)
                if poi > 0:
                    cover_frac = p_doi / poi
                break

    # 5. Non-Expiry Session
    if not is_expiry:
        score = 22
        if cw_testing or pw_testing:
            score += 6
        if regime == "NEGATIVE" or (flip > 0 and spot < flip):
            score += 4
        score = max(10, min(32, score))

        dte_str = f"{dte_now:.1f}d" if (dte_now is not None and dte_now > 0) else "off-cycle"
        return {
            "state": "NO_TRIGGER",
            "badge": "GAMMA BLAST: INACTIVE (NON-EXPIRY)",
            "description": f"Non-expiry session ({dte_str} to nearest expiry). 0-DTE blast inactive.",
            "score": score,
            "is_expiry": False,
            "in_blast_window": in_blast_window,
            "primary_strike": primary_strike,
        }

    # 6. Expiry Session: Pre-Blast Window (< 13:00 IST)
    if not in_blast_window and ist_min < 13 * 60:
        base_score = 24
        if cw_testing or pw_testing:
            base_score = 38
            state = "COILING"
            badge = "GAMMA BLAST: COILING (PRE-BLAST)"
            desc = f"0-DTE morning session. Spot testing {'Call' if cw_testing else 'Put'} barrier ({primary_strike:,.0f}). Blast acceleration window opens at 13:30 IST."
        else:
            state = "NO_TRIGGER"
            badge = "GAMMA BLAST: INACTIVE (MORNING)"
            desc = "0-DTE morning. No blast trigger yet. The blast window is 13:30–15:15 IST."
        return {
            "state": state,
            "badge": badge,
            "description": desc,
            "score": base_score,
            "is_expiry": True,
            "in_blast_window": False,
            "primary_strike": primary_strike,
        }

    # 7. Expiry Session: Afternoon Blast Window (13:00 - 15:30 IST)
    # Ignition points (0 to 40)
    if cover_frac < 0:
        # Writers covering! Saturates at 30% covering
        ignition_pts = min(40.0, (-cover_frac / _FULL_COVER) * 40.0)
    elif cover_frac > 0.05:
        # Writers actively defending (adds resistance)
        ignition_pts = 0.0
    else:
        ignition_pts = 12.0

    # Proximity & Breach points (0 to 40)
    if cw_breach:
        progress = min(1.0, max(0.0, (spot - call_wall + 10.0) / (step * 0.8)))
        proximity_pts = 26.0 + progress * 14.0
    elif pw_breach:
        progress = min(1.0, max(0.0, (put_wall + 10.0 - spot) / (step * 0.8)))
        proximity_pts = 26.0 + progress * 14.0
    elif cw_testing:
        proximity_pts = 14.0 + (1.0 - max(0.0, cw_dist) / (step * 0.6)) * 12.0
    elif pw_testing:
        proximity_pts = 14.0 + (1.0 - max(0.0, pw_dist) / (step * 0.6)) * 12.0
    else:
        proximity_pts = 6.0

    # Regime & Accelerator points (0 to 20)
    regime_pts = 0.0
    if regime == "NEGATIVE":
        regime_pts += 10.0
    if flip > 0 and spot < flip:
        regime_pts += 10.0
    elif net_gex < -100_000_000.0:
        regime_pts += 5.0

    raw_score = round(ignition_pts + proximity_pts + regime_pts)

    # Defending cap: if writers are actively adding OI at K, blast cannot sustain
    if cover_frac > 0.05:
        raw_score = min(raw_score, 34)

    score = max(12, min(96, raw_score))

    if score >= 60:
        state = "TRIGGERED"
        if cw_breach or spot > (call_wall + put_wall) / 2:
            badge = "GAMMA BLAST: ACTIVE"
            desc = f"Spot is through the call-gamma strike at ₹{call_wall:,.0f}. That is a location on the book, not a measured hedge."
        else:
            badge = "GAMMA BLAST: ACTIVE"
            desc = f"Spot is through the put-gamma strike at ₹{put_wall:,.0f}. That is a location on the book, not a measured hedge."
    elif score >= 35:
        state = "COILING"
        badge = "GAMMA BLAST: COILING / IMMINENT"
        target_name = "Call Wall" if cw_testing else ("Put Wall" if pw_testing else "barrier")
        desc = f"Spot is within {abs(spot - (primary_strike or spot)):.0f} pts of the {target_name}. Nearness is a map, not a coil that must break."
    else:
        state = "NO_TRIGGER"
        badge = "GAMMA BLAST: INACTIVE"
        desc = f"No blast trigger. Put-gamma strike {put_wall:,.0f}, call-gamma strike {call_wall:,.0f}. Concentration is a map, not a pin."

    return {
        "state": state,
        "badge": badge,
        "description": desc,
        "score": score,
        "is_expiry": True,
        "in_blast_window": in_blast_window,
        "primary_strike": primary_strike,
    }
