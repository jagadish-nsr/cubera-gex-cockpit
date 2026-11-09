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
from pathlib import Path
import os
import threading

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


def secondary_sustain_grade(
    *,
    cover_frac: float,
    cover_known: bool,
    pin_geometry: bool = False,
    prem_exp: float | None = None,
    wall_ratio: float | None = None,
    wall_frac: float | None = None,
) -> dict | None:
    """Research true-vs-fake grade as a *secondary* read on the live card.

    Does not replace the location/covering dashboard score. Collapsed walls
    (call == put) force an intact-wall path block so pin days grade FAKE/WEAK
    even when the location score is ACTIVE. None until cover tape exists.
    """
    if not cover_known:
        return None
    wr, wf = wall_ratio, wall_frac
    if pin_geometry:
        # Shared strike = no clear path ahead; treat as a large intact wall.
        wr = max(float(wr or 0.0), 3.0)
        wf = 0.0 if wf is None else float(wf)
    graded = grade_blast(cover_frac, prem_exp, wr, wf)
    if pin_geometry:
        # Location score may still be ACTIVE; sustain read must not say TRUE.
        graded = dict(graded)
        graded["grade"] = "FAKE-leaning (pin/cap)"
        graded["path"] = 0.0
        graded["score"] = min(int(graded["score"]), 34)
    return graded


def _wall_ahead_from_book(
    oi_strikes: list,
    primary: float,
    side: str,
    step: float,
) -> tuple[float | None, float | None]:
    """Same-side OI at the next listed strike vs primary (live path component)."""
    if not oi_strikes or primary <= 0 or step <= 0:
        return None, None
    ahead_k = primary + step if side == "call" else primary - step
    by_k: dict[float, dict] = {}
    for row in oi_strikes:
        try:
            by_k[float(row["strike"])] = row
        except (KeyError, TypeError, ValueError):
            continue
    prim = by_k.get(float(primary))
    ahead = by_k.get(float(ahead_k))
    if not prim or not ahead:
        return None, None
    key = f"{side}_oi"
    try:
        oi_p = float(prim.get(key) or 0.0)
        oi_a = float(ahead.get(key) or 0.0)
    except (TypeError, ValueError):
        return None, None
    if oi_p <= 0 or oi_a <= 0:
        return None, None
    return oi_a / oi_p, None  # drift unknown on a single snapshot


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


# A blast is a few minutes of covering. The day's open-interest change stays
# positive on a normal expiry morning, and that must not count as defense.
_COVER_MIN_BASE = timedelta(minutes=3)
_COVER_WINDOW = timedelta(minutes=10)
_COVER_KEEP = timedelta(minutes=30)
_COVER_SAMPLE_GAP = timedelta(seconds=45)
_OI_HISTORY: dict[str, list[tuple[datetime, dict[float, tuple[float, float]]]]] = {}
_OI_LOCK = threading.Lock()


def reset_strike_oi_history() -> None:
    """Drop the in-process strike-OI tape. Tests start from an empty book."""
    with _OI_LOCK:
        _OI_HISTORY.clear()


def note_strike_oi(symbol: str, oi_strikes, now: datetime) -> None:
    """Remember absolute call and put OI at each strike.

    A second print inside 45 seconds replaces the latest sample, so a cockpit
    poll does not fill the tape. Samples older than 30 minutes are dropped.
    """
    book: dict[float, tuple[float, float]] = {}
    for row in oi_strikes or []:
        strike = row.get("strike")
        if strike is None:
            continue
        try:
            book[float(strike)] = (
                float(row.get("call_oi") or 0.0),
                float(row.get("put_oi") or 0.0),
            )
        except (TypeError, ValueError):
            continue
    if not book or now is None:
        return
    key = (symbol or "NIFTY").upper()
    with _OI_LOCK:
        hist = _OI_HISTORY.setdefault(key, [])
        if hist and now - hist[-1][0] < _COVER_SAMPLE_GAP:
            hist[-1] = (now, book)
        else:
            hist.append((now, book))
        cutoff = now - _COVER_KEEP
        while hist and hist[0][0] < cutoff:
            hist.pop(0)


def window_cover_frac(symbol: str, strike: float, side: str, now: datetime) -> float | None:
    """OI change at ``strike`` over the last 3–10 minutes, as a fraction of the base.

    Positive means open interest rose (writers adding). Negative means it fell
    (covering). None until a sample at least 3 minutes old exists. The day's
    open-interest change is not this number.
    """
    if strike is None or now is None:
        return None
    idx = 0 if side == "call" else 1
    key = (symbol or "NIFTY").upper()
    target = now - _COVER_WINDOW
    with _OI_LOCK:
        hist = list(_OI_HISTORY.get(key) or [])
    if len(hist) < 2:
        return None
    latest = hist[-1][1].get(float(strike))
    if not latest or latest[idx] <= 0:
        return None
    base = None
    base_at = None
    for ts, book in hist:
        if now - ts < _COVER_MIN_BASE:
            continue
        row = book.get(float(strike))
        if not row or row[idx] <= 0:
            continue
        if base is None or abs((ts - target).total_seconds()) < abs((base_at - target).total_seconds()):
            base = row[idx]
            base_at = ts
    if base is None:
        return None
    return (latest[idx] - base) / base


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
    oi_strikes = list(getattr(dash, "oi_strikes", None) or [])
    # Prefer the 10s nearest-expiry blast lane for covering/premium when it is live —
    # the full-window chain is minutes-old fuel on a 0-DTE afternoon.
    try:
        from app.blast_ticker import latest as _blast_lane

        lane = _blast_lane(symbol)
    except Exception:
        lane = None
    if lane:
        by_k = {
            float(r["strike"]): dict(r)
            for r in oi_strikes
            if r.get("strike") is not None
        }
        for k, cell in lane.items():
            try:
                strike = float(k)
            except (TypeError, ValueError):
                continue
            if not isinstance(cell, dict):
                continue
            row = by_k.get(strike, {"strike": strike})
            for fld in ("call_oi", "put_oi", "call_ltp", "put_ltp"):
                if cell.get(fld) is not None:
                    row[fld] = cell[fld]
            by_k[strike] = row
        oi_strikes = list(by_k.values())

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
    # Afternoon watch runs from 13:00 through the F&O close (15:40).
    import config
    fno_close = int(getattr(config, "FNO_CLOSE_MIN", 15 * 60 + 40))
    in_blast_window = 13 * 60 <= ist_min < fno_close

    # Off-market hours safety gate: the blast watch is an open-session read.
    if not config.is_market_open(dt_ist):
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

    # 4. Covering is the change in strike OI over the last few minutes.
    # The day's call_doi stays positive after a normal morning build, and that
    # is not writers defending the blast.
    note_strike_oi(symbol, oi_strikes, dt_ist)
    cover_frac = 0.0
    primary_strike = None
    cover_side = "call"
    if cw_testing or (cw_dist <= pw_dist and call_wall > 0):
        primary_strike = call_wall
        cover_side = "call"
    elif pw_testing or put_wall > 0:
        primary_strike = put_wall
        cover_side = "put"
    cover_known = False
    if primary_strike:
        recent = window_cover_frac(symbol, float(primary_strike), cover_side, dt_ist)
        if recent is not None:
            cover_frac = recent
            cover_known = True

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

    raw_flags = getattr(levels, "flags", None)
    flags = raw_flags if isinstance(raw_flags, (list, tuple)) else []
    if any("near book expired" in str(flag) for flag in flags):
        return {
            "state": "NO_TRIGGER",
            "badge": "GAMMA BLAST: INACTIVE",
            "description": "Near gamma has gone to dust. No strike is named as a wall.",
            "score": 12,
            "is_expiry": True,
            "in_blast_window": in_blast_window,
            "primary_strike": None,
        }

    # 6. Expiry Session: Pre-Blast Window (< 13:00 IST)
    if not in_blast_window and ist_min < 13 * 60:
        base_score = 24
        if cw_testing or pw_testing:
            base_score = 38
            state = "COILING"
            badge = "GAMMA BLAST: COILING (PRE-BLAST)"
            desc = f"0-DTE morning session. Spot testing {'Call' if cw_testing else 'Put'} barrier ({primary_strike:,.0f}). Blast acceleration window opens at 13:00 IST."
        else:
            state = "NO_TRIGGER"
            badge = "GAMMA BLAST: INACTIVE (MORNING)"
            desc = "0-DTE morning. No blast trigger yet. The blast window is 13:00–15:40 IST."
        return {
            "state": state,
            "badge": badge,
            "description": desc,
            "score": base_score,
            "is_expiry": True,
            "in_blast_window": False,
            "primary_strike": primary_strike,
        }

    # 7. Expiry Session: Afternoon Blast Window (13:00 through the F&O close)
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

    # Defending cap: open interest rising at the strike over the last few minutes.
    if cover_known and cover_frac > 0.05:
        raw_score = min(raw_score, 34)
    # A breach with no recent open-interest tape is a location, not a blast yet.
    elif not cover_known:
        raw_score = min(raw_score, 59)

    score = max(12, min(96, raw_score))

    # Collapsed walls (call == put) are pin/compress geometry — same score math,
    # different badge so ACTIVE is not read as an expansion cue.
    pin_geometry = (
        call_wall > 0
        and put_wall > 0
        and abs(call_wall - put_wall) < 1e-6
    )

    # Secondary research grade (sustain vs pin). Location score stays primary.
    wall_ratio, wall_frac = None, None
    if primary_strike and cover_side:
        wall_ratio, wall_frac = _wall_ahead_from_book(
            oi_strikes, float(primary_strike), cover_side, step,
        )
    sustain = secondary_sustain_grade(
        cover_frac=cover_frac,
        cover_known=cover_known,
        pin_geometry=pin_geometry,
        prem_exp=None,  # LTP history not in the short-window OI tape yet
        wall_ratio=wall_ratio,
        wall_frac=wall_frac,
    )
    sustain_score = None if sustain is None else sustain.get("score")
    sustain_grade = None if sustain is None else sustain.get("grade")

    if score >= 60:
        state = "TRIGGERED"
        if pin_geometry:
            badge = "GAMMA PIN: COMPRESS"
            desc = (
                f"Call and put gamma concentrate at ₹{call_wall:,.0f}. "
                "That is a location and concentration map, not an expansion cue."
            )
        elif cw_breach or spot > (call_wall + put_wall) / 2:
            badge = "GAMMA BLAST: ACTIVE"
            desc = f"Spot is through the call-gamma strike at ₹{call_wall:,.0f}. That is a location on the book, not a measured hedge."
        else:
            badge = "GAMMA BLAST: ACTIVE"
            desc = f"Spot is through the put-gamma strike at ₹{put_wall:,.0f}. That is a location on the book, not a measured hedge."
    elif score >= 35:
        state = "COILING"
        if pin_geometry:
            badge = "GAMMA PIN: COILING"
            desc = (
                f"Spot is within {abs(spot - call_wall):.0f} pts of the shared "
                f"₹{call_wall:,.0f} gamma strike. Nearness is a map, not a coil that must break."
            )
        else:
            badge = "GAMMA BLAST: COILING / IMMINENT"
            target_name = "Call Wall" if cw_testing else ("Put Wall" if pw_testing else "barrier")
            desc = f"Spot is within {abs(spot - (primary_strike or spot)):.0f} pts of the {target_name}. Nearness is a map, not a coil that must break."
    else:
        state = "NO_TRIGGER"
        badge = "GAMMA BLAST: INACTIVE"
        desc = f"No blast trigger. Put-gamma strike {put_wall:,.0f}, call-gamma strike {call_wall:,.0f}. Concentration is a map, not a pin."

    if sustain_grade and state == "TRIGGERED":
        desc = (
            f"{desc} Sustain read: {sustain_grade} "
            f"(research grade {sustain_score}/100 — not a direction call)."
        )

    return {
        "state": state,
        "badge": badge,
        "description": desc,
        "score": score,
        "is_expiry": True,
        "in_blast_window": in_blast_window,
        "primary_strike": primary_strike,
        "sustain_score": sustain_score,
        "sustain_grade": sustain_grade,
        "pin_geometry": pin_geometry,
    }


_SCORE_FIELDS = (
    "timestamp_utc", "time_ist", "date_ist", "symbol", "spot",
    "score", "state", "primary_strike", "badge",
    "call_wall", "put_wall", "in_blast_window",
)
_score_written: dict[str, tuple] = {}
BLAST_SCORE_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "journal" / "gamma_blast_score"
)


def journal_blast_read(symbol: str, spot: float, result: dict, fetched_at=None) -> bool:
    """Append one card reading per IST minute (headless + UI share this path).

    At most one row per symbol per minute so a cockpit poll storm does not
    duplicate the tape, while a flat COILING stretch still leaves a minute
    grid for later intelligence. Returns True when a row was written.
    Directory: ``data/journal/gamma_blast_score/<SYMBOL>``.
    """
    import csv

    if not result or result.get("score") is None:
        return False
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)
    elif getattr(fetched_at, "tzinfo", None) is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    ist = fetched_at.astimezone(timezone(timedelta(hours=5, minutes=30)))
    minute = ist.strftime("%H:%M")
    day = ist.strftime("%Y-%m-%d")
    score = result.get("score")
    state = result.get("state")
    strike = result.get("primary_strike")
    key = (day, minute)  # one print per minute — trajectory, not only changes
    sym = (symbol or "NIFTY").upper()
    if _score_written.get(sym) == key:
        return False
    levels = result.get("levels") or {}
    call_wall = result.get("call_wall", levels.get("call_wall") if isinstance(levels, dict) else None)
    put_wall = result.get("put_wall", levels.get("put_wall") if isinstance(levels, dict) else None)
    directory = BLAST_SCORE_DIR / sym
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day}.csv"
    new_file = not path.exists()
    row = {
        "timestamp_utc": fetched_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_ist": minute,
        "date_ist": day,
        "symbol": sym,
        "spot": round(float(spot), 2) if spot else "",
        "score": score,
        "state": state,
        "primary_strike": "" if strike is None else strike,
        "badge": result.get("badge") or "",
        "call_wall": "" if call_wall in (None, "") else call_wall,
        "put_wall": "" if put_wall in (None, "") else put_wall,
        "in_blast_window": int(bool(result.get("in_blast_window"))),
    }
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_SCORE_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    _score_written[sym] = key
    return True


def journal_blast_from_dashboard(dash, symbol: str = "NIFTY") -> bool:
    """Evaluate + journal from a live pull (timeseries / blast loop). Fail soft."""
    try:
        result = evaluate_dashboard_gamma_blast(dash, symbol)
    except Exception:
        return False
    if not result.get("is_expiry"):
        return False
    levels = getattr(dash, "levels", None)
    spot = float(getattr(levels, "spot", 0.0) or 0.0)
    # Persist walls beside the score so later studies need no second join.
    result = dict(result)
    result["call_wall"] = float(getattr(levels, "call_wall", 0.0) or 0.0) or None
    result["put_wall"] = float(getattr(levels, "put_wall", 0.0) or 0.0) or None
    try:
        return journal_blast_read(symbol, spot, result, getattr(dash, "fetched_at", None))
    except Exception:
        return False
