"""Expiry gamma-blast → recommendation risk conditioner (map, not a direction call).

Pure helpers. Live state comes from ``evaluate_dashboard_gamma_blast`` (same as
the cockpit card). Size multipliers are tunables; they do not raise quality
score for CE vs PE. Decision-support only — no auto-execution.
"""
from __future__ import annotations

from typing import Any


# Tunable later when more clean expiry days exist.
# Calibrated risk-only from Sensex 5 Nov + Nifty 9 Nov first-print forwards:
# pin/compress and FAKE-leaning sustain → cut harder; never raise CE vs PE quality.
SIZE_MULT_COILING = 0.75
SIZE_MULT_ACTIVE = 0.50
SIZE_MULT_PIN = 0.40          # collapsed call==put walls (Nifty 9 close geometry)
SIZE_MULT_FAKE_SUSTAIN = 0.45  # ACTIVE but research sustain FAKE/WEAK — still no direction
SHORT_NEAR_MAGNET_PENALTY = 4  # quality points; ACTIVE short-premium near magnet only


def _truthy(val) -> bool:
    if isinstance(val, bool):
        return val
    if val in (None, "", 0, "0", "false", "False"):
        return False
    return bool(val)


def _fake_leaning_sustain(gb: dict) -> bool:
    grade = str(gb.get("sustain_grade") or "").upper()
    if not grade:
        return False
    return "FAKE" in grade or "WEAK" in grade


def blast_risk_adjust(blast: dict | None, spot: float | None = None) -> dict[str, Any]:
    """Map a blast card dict to size/risk annotations.

    Returns
    -------
    active, coiling, pin : bool
    size_mult : float  (1.0 / 0.75 / 0.50 / 0.45 / 0.40)
    tag : short label for rationale lines
    risk_line : one plain sentence for risk notes
    applies : True when expiry + coiling/active (callers may still read tag="")

    Closed-loop from the two audited expiries is **risk-only**: pin geometry and
    FAKE/WEAK sustain cut size further. They do **not** boost CE vs PE quality.
    """
    gb = blast or {}
    if not gb.get("is_expiry"):
        return {
            "active": False,
            "coiling": False,
            "pin": False,
            "size_mult": 1.0,
            "tag": "",
            "risk_line": "",
            "applies": False,
            "primary_strike": None,
            "score": None,
        }

    state = str(gb.get("state") or "").upper()
    badge = str(gb.get("badge") or "").upper()
    score = gb.get("score")
    try:
        score_n = int(score) if score is not None else None
    except (TypeError, ValueError):
        score_n = None

    pin = _truthy(gb.get("pin_geometry")) or "GAMMA PIN" in badge or ": COMPRESS" in badge

    active = (
        state == "TRIGGERED"
        or ": ACTIVE" in badge
        or (pin and "COMPRESS" in badge and score_n is not None and score_n >= 60)
        or (score_n is not None and score_n >= 60 and state not in ("COILING", "NO_TRIGGER"))
    )
    # Prefer explicit COILING; do not treat ACTIVE as coiling.
    coiling = (not active) and (
        state == "COILING" or "COILING" in badge
        or (score_n is not None and 35 <= score_n < 60)
    )

    primary = gb.get("primary_strike")
    try:
        primary_f = float(primary) if primary is not None else None
    except (TypeError, ValueError):
        primary_f = None
    if primary_f is not None and primary_f <= 0:
        primary_f = None

    spot_f = None
    try:
        spot_f = float(spot) if spot is not None else None
    except (TypeError, ValueError):
        spot_f = None

    fake_sustain = _fake_leaning_sustain(gb)

    if active:
        # Pin/compress cuts hardest (map concentration, not an expansion cue).
        if pin:
            size_mult = SIZE_MULT_PIN
            kind = "PIN/COMPRESS"
        elif fake_sustain:
            size_mult = SIZE_MULT_FAKE_SUSTAIN
            kind = "ACTIVE (FAKE/WEAK sustain)"
        else:
            size_mult = SIZE_MULT_ACTIVE
            kind = "ACTIVE"
        loc = ""
        if primary_f and spot_f and spot_f > 0:
            loc = f" Spot is through / at the gamma strike ₹{primary_f:,.0f}."
        elif primary_f:
            loc = f" Primary gamma strike ₹{primary_f:,.0f}."
        tag = f"BLAST {kind}{f' {score_n}' if score_n is not None else ''}"
        if pin:
            risk_line = (
                f"Expiry gamma PIN/COMPRESS (score {score_n if score_n is not None else '—'}). "
                f"Call and put gamma share a strike — concentration on the map, not an expansion cue."
                f"{loc} Size cut to {int(size_mult * 100)}% of base risk. "
                f"Shorts stay beyond the walls, never on the magnet."
            )
        else:
            risk_line = (
                f"Expiry blast {kind} (score {score_n if score_n is not None else '—'}). "
                f"That is a location on the book, not a measured hedge.{loc} "
                f"Size cut to {int(size_mult * 100)}% of base risk. Shorts stay beyond the walls, never on the magnet."
            )
    elif coiling:
        size_mult = SIZE_MULT_PIN if pin else SIZE_MULT_COILING
        near = ""
        if primary_f and spot_f and spot_f > 0:
            near = f" Spot is within {abs(spot_f - primary_f):.0f} pts of ₹{primary_f:,.0f}."
        elif primary_f:
            near = f" Near gamma strike ₹{primary_f:,.0f}."
        tag = (
            f"BLAST PIN COILING{f' {score_n}' if score_n is not None else ''}"
            if pin
            else f"BLAST COILING{f' {score_n}' if score_n is not None else ''}"
        )
        risk_line = (
            f"Expiry blast COILING (score {score_n if score_n is not None else '—'})."
            f"{near} Nearness is a map, not a coil that must break. "
            f"Size cut to {int(size_mult * 100)}% of base risk."
        )
    else:
        return {
            "active": False,
            "coiling": False,
            "pin": pin,
            "size_mult": 1.0,
            "tag": "",
            "risk_line": "",
            "applies": False,
            "primary_strike": primary_f,
            "score": score_n,
        }

    return {
        "active": active,
        "coiling": coiling,
        "pin": pin,
        "size_mult": size_mult,
        "tag": tag,
        "risk_line": risk_line,
        "applies": True,
        "primary_strike": primary_f,
        "score": score_n,
    }


def short_premium_near_magnet(
    idea: Any,
    primary_strike: float | None,
    step: float,
) -> bool:
    """True when a short-premium style play sits on/near the blast primary strike."""
    if primary_strike is None or step <= 0:
        return False
    badge = str(getattr(idea, "play_badge", "") or "").upper()
    # Long-premium / break ideas are not short-premium near the magnet.
    if badge in ("MOMENTUM", "STRUCTURE", "STRIKE BREAK", "VOL DISPERSION"):
        return False
    if badge not in ("VANNA-CHARM", "RATIO REVERSAL", "ICEBERG FADE"):
        # Unknown badge: only flag if strike is exactly on the magnet band
        pass
    try:
        strike = float(getattr(idea, "strike", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if strike <= 0:
        return False
    return abs(strike - float(primary_strike)) <= float(step)


def apply_active_short_magnet_penalty(
    idea: Any,
    adj: dict[str, Any],
    step: float,
) -> int:
    """Optional tiny quality penalty when ACTIVE and short-premium near magnet.

    Returns the new quality_score (unchanged if N/A). Never raises the score.
    """
    if not adj.get("active"):
        return int(getattr(idea, "quality_score", 0) or 0)
    if not short_premium_near_magnet(idea, adj.get("primary_strike"), step):
        return int(getattr(idea, "quality_score", 0) or 0)
    old = int(getattr(idea, "quality_score", 0) or 0)
    new = max(0, old - SHORT_NEAR_MAGNET_PENALTY)
    idea.quality_score = new
    reason = getattr(idea, "score_reason", "") or ""
    note = f"−{SHORT_NEAR_MAGNET_PENALTY} ACTIVE short near gamma strike (map risk)"
    idea.score_reason = f"{reason}; {note}".strip("; ") if reason else note
    return new
