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
