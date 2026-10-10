"""analytics/directional_bias.py — IOAS v3.0 §3.1 Directional Bias Score.

Aggregates 8 normalised sub-signals into a score from -10.0 (Extreme Bearish)
to +10.0 (Extreme Bullish).

    Bias = Σ w_i × s_i   where Σ|w_i| = 10.0

Sub-signal | Weight | Source
-----------+--------+-------
s1  PCR Level              1.5   oi_volume (pcr_oi)
s2  PCR Trend (15m)        1.0   oi_volume history (passed in)
s3  ATM IV Skew            1.5   iv_skew module
s4  Wall Alignment         1.5   levels.call_wall / put_wall / spot
s5  Max Pain Gravitation   1.0   levels.max_pain / spot (bps)
s6  OI Velocity Bias       1.5   oi_volume call_doi / put_doi
s7  Strike Buildup Dom.    1.0   oi_strikes (call_doi / put_doi per strike)
s8  IV Rank / Regime       1.0   india_vix / atm_iv

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
    """PCR Level: >1.25 bullish, <0.85 bearish."""
    if pcr is None or pcr != pcr:
        return 0.0, "PCR Level — no data", ""
    if pcr > 1.25:
        return 1.0, f"PCR {pcr:.2f} (put-heavy → bullish)", f"PCR={pcr:.2f} >1.25"
    if pcr < 0.85:
        return -1.0, f"PCR {pcr:.2f} (call-heavy → bearish)", f"PCR={pcr:.2f} <0.85"
    # Linear interpolation in neutral band
    mid = (pcr - 0.85) / (1.25 - 0.85)  # 0…1 across neutral band
    val = (mid - 0.5) * 2.0              # -1…+1 linearly
    return round(val, 3), f"PCR {pcr:.2f} (neutral)", f"PCR={pcr:.2f}"


def _s2_pcr_trend(pcr_now: Optional[float], pcr_15m_ago: Optional[float]) -> tuple[float, str, str]:
    """PCR Trend over 15m: Δ>+0.05 bullish, Δ<-0.05 bearish."""
    if pcr_now is None or pcr_15m_ago is None:
        return 0.0, "PCR Trend — no history", ""
    delta = pcr_now - pcr_15m_ago
    detail = f"ΔPCR={delta:+.3f}"
    if delta > 0.05:
        return 1.0, f"PCR rising +{delta:.3f} (bullish)", detail
    if delta < -0.05:
        return -1.0, f"PCR falling {delta:+.3f} (bearish)", detail
    val = delta / 0.05  # -1…+1 within neutral band
    return round(max(-1.0, min(1.0, val)), 3), f"PCR trend flat ({delta:+.3f})", detail


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
    """Wall Alignment: spot > midpoint(call_wall, put_wall) → bullish."""
    if not call_wall or not put_wall:
        return 0.0, "Wall Alignment — walls not available", ""
    mid = (call_wall + put_wall) / 2.0
    band = (call_wall - put_wall) * 0.10  # 10% of range = neutral zone
    detail = f"Spot={spot:,.0f} mid={mid:,.0f} CW={call_wall:,.0f} PW={put_wall:,.0f}"
    if spot > mid + band:
        return 1.0, "Spot above wall midpoint (bullish bias)", detail
    if spot < mid - band:
        return -1.0, "Spot below wall midpoint (bearish bias)", detail
    val = (spot - mid) / max(band, 1.0)
    return round(max(-1.0, min(1.0, val)), 3), "Spot near wall midpoint (neutral)", detail


def _s5_maxpain_grav(spot: float, max_pain: Optional[float], band_bps: float = 50.0) -> tuple[float, str, str]:
    """Max Pain Gravitation: Spot < MaxPain − band_bps → bullish (upward pull).
    band_bps is normalised basis-points (50 bps default).
    For Nifty@24500: 50bps ≈ 122pts. For Sensex@80000: 50bps ≈ 400pts."""
    if not max_pain or not spot:
        return 0.0, "Max Pain — not available", ""
    dist_bps = (max_pain - spot) / spot * 10_000  # basis points
    detail = f"MaxPain={max_pain:,.0f} dist={dist_bps:+.1f}bps"
    if dist_bps > band_bps:
        return 1.0, f"Spot below max pain by {dist_bps:.0f}bps (upward pull)", detail
    if dist_bps < -band_bps:
        return -1.0, f"Spot above max pain by {abs(dist_bps):.0f}bps (downward pull)", detail
    val = dist_bps / band_bps
    return round(max(-1.0, min(1.0, val)), 3), f"Spot near max pain ({dist_bps:+.0f}bps)", detail


def _s6_oi_velocity(call_doi: Optional[float], put_doi: Optional[float]) -> tuple[float, str, str]:
    """OI Velocity Bias: Put OI adding faster than Call OI → bullish."""
    cd = call_doi or 0.0
    pd = put_doi or 0.0
    total = abs(cd) + abs(pd)
    if total < 1:
        return 0.0, "OI Velocity — insufficient data", ""
    ratio = pd / total  # 0…1; 0.5 = balanced, >0.5 = more put OI building
    detail = f"ΔCOI={cd:+,.0f} ΔPOI={pd:+,.0f}"
    if ratio > 0.6:
        return 1.0, f"Put OI velocity dominant ({ratio:.0%} of flow → bullish)", detail
    if ratio < 0.4:
        return -1.0, f"Call OI velocity dominant ({1-ratio:.0%} of flow → bearish)", detail
    val = (ratio - 0.5) * 2.0  # -1…+1
    return round(max(-1.0, min(1.0, val)), 3), f"OI velocity balanced ({ratio:.0%} put)", detail


def _s7_buildup_dom(oi_strikes: Optional[list[dict]]) -> tuple[float, str, str]:
    """Strike Buildup Dominance: count of Long Buildup strikes > Short Buildup → bullish."""
    if not oi_strikes:
        return 0.0, "Strike Buildup — no per-strike data", ""
    long_b = short_b = 0
    for s in oi_strikes:
        cd = s.get("call_doi") or 0.0
        pd = s.get("put_doi") or 0.0
        if pd > 0 and cd <= 0:    # Put OI building, call unwinding → bullish (protective puts)
            long_b += 1
        elif cd > 0 and pd <= 0:  # Call OI building, put unwinding → bearish (call resistance)
            short_b += 1
    total = long_b + short_b
    if total == 0:
        return 0.0, "Strike Buildup — no directional dominance", f"long={long_b} short={short_b}"
    detail = f"Long-buildup={long_b} Short-buildup={short_b}"
    ratio = (long_b - short_b) / total
    if ratio > 0.25:
        return 1.0, f"Long buildup dominant ({long_b} vs {short_b} strikes → bullish)", detail
    if ratio < -0.25:
        return -1.0, f"Short buildup dominant ({short_b} vs {long_b} strikes → bearish)", detail
    return round(ratio * 4.0, 3), f"Balanced buildup ({long_b} long / {short_b} short)", detail


def _s8_iv_rank(india_vix: Optional[dict], near_atm_iv: Optional[float]) -> tuple[float, str, str]:
    """IV Rank / Regime: IV Rank <30 with upward drift → bullish, >70 with downside → bearish.
    Uses India VIX as proxy for IV rank."""
    vix_val = None
    if india_vix and isinstance(india_vix, dict):
        vix_val = india_vix.get("last")
    elif isinstance(india_vix, (int, float)):
        vix_val = float(india_vix)
    if vix_val is None and near_atm_iv is not None:
        vix_val = near_atm_iv * 100  # convert decimal to percentage

    if vix_val is None:
        return 0.0, "IV Rank — no VIX data", ""

    detail = f"India VIX={vix_val:.1f}"
    # India VIX typical range: 10–30
    # Low VIX (<12): IV rank low → market calm → bullish lean
    # High VIX (>20): IV rank high → fear → bearish lean
    if vix_val < 12.0:
        return 1.0, f"VIX {vix_val:.1f} (low — calm regime, bullish lean)", detail
    if vix_val > 22.0:
        return -1.0, f"VIX {vix_val:.1f} (high — fear regime, bearish lean)", detail
    # Linear interpolation
    val = -(vix_val - 17.0) / 5.0   # VIX=12 → +1, VIX=22 → -1, VIX=17 → 0
    return round(max(-1.0, min(1.0, val)), 3), f"VIX {vix_val:.1f} (mid-range, neutral)", detail


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


def _regime_label(score: float) -> str:
    if score >= 7.0:   return "Extreme Bullish 🚀"
    if score >= 4.0:   return "Bullish 📈"
    if score >= 1.5:   return "Mild Bullish"
    if score <= -7.0:  return "Extreme Bearish 💥"
    if score <= -4.0:  return "Bearish 📉"
    if score <= -1.5:  return "Mild Bearish"
    return "Neutral ⏳"


def _headline(score: float, regime: str, signals: list[SubSignal]) -> str:
    top = sorted(signals, key=lambda s: abs(s.contribution), reverse=True)[:2]
    drivers = " + ".join(s.label.split("(")[0].strip() for s in top)
    return f"{regime} ({score:+.1f}/10) — driven by {drivers}"
