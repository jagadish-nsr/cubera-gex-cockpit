"""analytics/directional_bias.py — book-balance bias score.

Eight rows are always returned and the weights still sum to 10.0 so the
table stays complete. Only skew (s3) and India VIX (s8) move the number.

PCR, the gamma-strike midpoint, max pain, and open-interest changes are
shown as facts. They do not add a bullish or bearish point. A gamma strike
is a concentration. Max pain is a settlement strike. Open interest is not
buyer-versus-seller flow.

Separation guarantee: no network, no Streamlit imports.
All inputs are plain Python objects / dicts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Weights — must sum to 10.0 in absolute terms (IOAS §3.1)
# ---------------------------------------------------------------------------
WEIGHTS: dict[str, float] = {
    "s1_pcr_level":      1.5,
    "s2_pcr_trend":      1.0,
    "s3_iv_skew":        1.5,
    "s4_wall_align":     1.5,
    "s5_maxpain_grav":   1.0,
    "s6_oi_velocity":    1.5,
    "s7_buildup_dom":    1.0,
    "s8_iv_rank":        1.0,
}
assert abs(sum(WEIGHTS.values()) - 10.0) < 1e-6, "Weights must sum to 10.0"


@dataclass
class SubSignal:
    key: str
    value: float          # normalised ∈ [-1.0, +1.0]
    weight: float
    contribution: float   # value × weight
    label: str            # plain-language read for this signal
    detail: str = ""      # raw numbers for the tooltip


@dataclass
class BiasResult:
    score: float                          # -10.0 … +10.0
    regime: str                           # "Extreme Bullish" … "Extreme Bearish"
    signals: list[SubSignal] = field(default_factory=list)
    headline: str = ""                    # one-liner


# ---------------------------------------------------------------------------
# Sub-signal evaluators  (each returns float in [-1, +1])
# ---------------------------------------------------------------------------

def _s1_pcr_level(pcr: Optional[float]) -> tuple[float, str, str]:
    """PCR is an open-interest mix. It does not move the score."""
    if pcr is None or pcr != pcr:
        return 0.0, "PCR — no data", ""
    if pcr > 1.25:
        mix = "put-heavy"
    elif pcr < 0.85:
        mix = "call-heavy"
    else:
        mix = "mixed"
    return 0.0, f"PCR {pcr:.2f} is {mix} open interest. Not a direction.", f"PCR={pcr:.2f}"


def _s2_pcr_trend(pcr_now: Optional[float], pcr_15m_ago: Optional[float]) -> tuple[float, str, str]:
    """A PCR change is a fact. It does not move the score."""
    if pcr_now is None or pcr_15m_ago is None:
        return 0.0, "PCR trend — no history", ""
    delta = pcr_now - pcr_15m_ago
    return 0.0, f"PCR moved {delta:+.3f} over 15 minutes. Not a direction.", f"ΔPCR={delta:+.3f}"


def _s3_iv_skew(skew_25d: Optional[float]) -> tuple[float, str, str]:
    """ATM IV Skew: put_iv−call_iv > +1.5 pts → bullish (call buying / upside demand).
    As decimal: 0.015 threshold."""
    if skew_25d is None:
        return 0.0, "IV Skew — no data", ""
    detail = f"25Δ skew={skew_25d*100:+.1f}%"
    # Positive skew = puts more expensive (fear), negative = calls more expensive
    # IOAS §3.1: Put IV > Call IV + 1.5pts → +1.0 (unusual upside demand)
    if skew_25d > 0.015:
        return 1.0, f"Put skew {skew_25d*100:+.1f}% (upside call demand)", detail
    if skew_25d < -0.015:
        return -1.0, f"Call skew {skew_25d*100:+.1f}% (downside fear bid)", detail
    val = skew_25d / 0.015
    return round(max(-1.0, min(1.0, val)), 3), f"IV skew neutral ({skew_25d*100:+.1f}%)", detail


def _s4_wall_align(spot: float, call_wall: Optional[float], put_wall: Optional[float]) -> tuple[float, str, str]:
    """Where spot sits between the gamma strikes. Location only. Score stays 0."""
    if not call_wall or not put_wall:
        return 0.0, "Gamma strikes — not available", ""
    mid = (call_wall + put_wall) / 2.0
    detail = f"Spot={spot:,.0f} mid={mid:,.0f} call-gamma={call_wall:,.0f} put-gamma={put_wall:,.0f}"
    if spot > mid:
        place = "above"
    elif spot < mid:
        place = "below"
    else:
        place = "at"
    return 0.0, f"Spot is {place} the midpoint of the gamma strikes. Location only.", detail


def _s5_maxpain_grav(spot: float, max_pain: Optional[float], band_bps: float = 50.0) -> tuple[float, str, str]:
    """Distance to the max-pain settlement strike. Not a pull. Score stays 0."""
    del band_bps
    if not max_pain or not spot:
        return 0.0, "Max pain — not available", ""
    dist = max_pain - spot
    detail = f"MaxPain={max_pain:,.0f} dist={dist:+.1f}pts"
    return 0.0, f"Max pain is {abs(dist):,.0f} pts from spot. A settlement strike, not a pull.", detail


def _s6_oi_velocity(call_doi: Optional[float], put_doi: Optional[float]) -> tuple[float, str, str]:
    """Open-interest change. Not buyer-versus-seller flow. Score stays 0."""
    if call_doi is None and put_doi is None:
        return 0.0, "Open interest change — no data", ""
    cd = call_doi or 0.0
    pd = put_doi or 0.0
    if abs(cd) + abs(pd) < 1:
        return 0.0, "Open interest change — insufficient data", ""
    detail = f"ΔCOI={cd:+,.0f} ΔPOI={pd:+,.0f}"
    return (
        0.0,
        f"Put open interest changed {pd:+,.0f}; call open interest changed {cd:+,.0f}. Not a direction.",
        detail,
    )


def _s7_buildup_dom(oi_strikes: Optional[list[dict]]) -> tuple[float, str, str]:
    """Which strikes added open interest. Not a direction. Score stays 0."""
    if not oi_strikes:
        return 0.0, "Strike buildup — no per-strike data", ""
    put_side = call_side = 0
    for s in oi_strikes:
        cd = s.get("call_doi") or 0.0
        pd = s.get("put_doi") or 0.0
        if pd > 0 and cd <= 0:
            put_side += 1
        elif cd > 0 and pd <= 0:
            call_side += 1
    detail = f"put-side={put_side} call-side={call_side}"
    return (
        0.0,
        f"Put-side buildup on {put_side} strikes, call-side on {call_side}. Not a direction.",
        detail,
    )


def _s8_iv_rank(india_vix: Optional[dict], near_atm_iv: Optional[float]) -> tuple[float, str, str]:
    """IV Rank / Regime: IV Rank <30 with upward drift → bullish, >70 with downside → bearish.
    Uses India VIX as proxy for IV rank."""
    vix_val = None
    source = "India VIX"
    if india_vix and isinstance(india_vix, dict):
        vix_val = india_vix.get("last")
    elif isinstance(india_vix, (int, float)):
        vix_val = float(india_vix)
    del near_atm_iv
    if vix_val is None:
        return 0.0, "India VIX unavailable", ""

    detail = f"{source}={vix_val:.1f}"
    # India VIX typical range: 10–30. The same bands apply to ATM IV in percent.
    if vix_val < 12.0:
        return 1.0, f"{source} {vix_val:.1f} (low, calm)", detail
    if vix_val > 22.0:
        return -1.0, f"{source} {vix_val:.1f} (high, stressed)", detail
    val = -(vix_val - 17.0) / 5.0   # 12 → +1, 22 → -1, 17 → 0
    return round(max(-1.0, min(1.0, val)), 3), f"{source} {vix_val:.1f} (mid-range, neutral)", detail


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_directional_bias(
    *,
    spot: float,
    call_wall: Optional[float],
    put_wall: Optional[float],
    max_pain: Optional[float],
    pcr: Optional[float],
    pcr_15m_ago: Optional[float] = None,
    call_doi: Optional[float] = None,
    put_doi: Optional[float] = None,
    oi_strikes: Optional[list] = None,
    india_vix: Optional[dict] = None,
    skew_25d: Optional[float] = None,
    near_atm_iv: Optional[float] = None,
) -> BiasResult:
    """Compute the IOAS v3.0 Directional Bias Score (-10.0 to +10.0).

    All parameters are optional/keyword-only; missing inputs produce 0-contribution
    sub-signals (fail-soft, never raises). Returns a BiasResult with per-sub-signal
    breakdown for the UI table.
    """
    evaluators = [
        ("s1_pcr_level",    _s1_pcr_level(pcr)),
        ("s2_pcr_trend",    _s2_pcr_trend(pcr, pcr_15m_ago)),
        ("s3_iv_skew",      _s3_iv_skew(skew_25d)),
        ("s4_wall_align",   _s4_wall_align(spot, call_wall, put_wall)),
        ("s5_maxpain_grav", _s5_maxpain_grav(spot, max_pain)),
        ("s6_oi_velocity",  _s6_oi_velocity(call_doi, put_doi)),
        ("s7_buildup_dom",  _s7_buildup_dom(oi_strikes)),
        ("s8_iv_rank",      _s8_iv_rank(india_vix, near_atm_iv)),
    ]

    signals: list[SubSignal] = []
    total = 0.0
    for key, (val, label, detail) in evaluators:
        w = WEIGHTS[key]
        contrib = val * w
        total += contrib
        signals.append(SubSignal(
            key=key, value=val, weight=w,
            contribution=contrib, label=label, detail=detail,
        ))

    score = round(max(-10.0, min(10.0, total)), 2)
    regime = _regime_label(score)
    headline = _headline(score, regime, signals)
    return BiasResult(score=score, regime=regime, signals=signals, headline=headline)


# Skew (weight 1.5) and India VIX (weight 1.0) are the only live contributors.
# These bands are the old 70% / 40% / 15% shares of that ±2.5 ceiling.
BIAS_CEILING = 2.5
BIAS_EXTREME = 1.75
BIAS_STRONG = 1.0
BIAS_MILD = 0.4
BIAS_QUIET = 0.6
BIAS_RATIO_LO = 0.25


def _regime_label(score: float) -> str:
    if score >= BIAS_EXTREME:   return "Extreme Bullish 🚀"
    if score >= BIAS_STRONG:    return "Bullish 📈"
    if score >= BIAS_MILD:      return "Mild Bullish"
    if score <= -BIAS_EXTREME:  return "Extreme Bearish 💥"
    if score <= -BIAS_STRONG:   return "Bearish 📉"
    if score <= -BIAS_MILD:     return "Mild Bearish"
    return "Neutral ⏳"


def _headline(score: float, regime: str, signals: list[SubSignal]) -> str:
    top = sorted(signals, key=lambda s: abs(s.contribution), reverse=True)[:2]
    drivers = " + ".join(s.label.split("(")[0].strip() for s in top)
    return f"{regime} ({score:+.1f}/10) — driven by {drivers}"
