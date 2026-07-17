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

# |call - put| within this many points counts as "at the crossover" (spot ≈ K).
CROSS_TOL_PTS = 5.0


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
