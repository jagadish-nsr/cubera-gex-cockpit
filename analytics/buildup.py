"""analytics/buildup.py — IOAS v3.0 §3.2 Strike Buildup Matrix.

Classifies each strike's OI change as one of four states:
  LONG_BUILDUP    — Price up + OI up   → new long positions
  SHORT_BUILDUP   — Price down + OI up → new short positions
  SHORT_COVERING  — Price up + OI down → shorts exiting (less conviction)
  LONG_UNWINDING  — Price down + OI down → longs exiting

Plus: identifies the Top-3 High-Conviction Buildup Strikes by OI velocity
(absolute ΔOI over 15m rolling window, per IOAS §3.2).

Separation guarantee: no network, no Streamlit. Inputs are plain dicts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Min OI thresholds (IOAS §3.2)
MIN_OI_NIFTY = 50_000
MIN_OI_SENSEX = 15_000


@dataclass
class StrikeBuildup:
    strike: float
    call_oi: float
    put_oi: float
    call_doi: float        # ΔOI calls (since open or 15m window)
    put_doi: float         # ΔOI puts
    call_classification: str   # LONG_BUILDUP | SHORT_BUILDUP | SHORT_COVERING | LONG_UNWINDING | NEUTRAL
    put_classification: str
    call_velocity: float   # |call_doi| — used for top-3 ranking
    put_velocity: float
    net_velocity: float    # |call_doi| + |put_doi|
    dominant: str          # "CALL" | "PUT" | "BALANCED"
    call_ltp: Optional[float] = None
    put_ltp: Optional[float] = None
    net_gex: Optional[float] = None


def classify_buildup(doi: float, price_up: Optional[bool]) -> str:
    """Classify a single leg's OI change given price direction.

    price_up: True = spot moved up in the period, False = down, None = unknown.
    doi > 0 means OI increased; doi < 0 means OI decreased.
    """
    if abs(doi) < 1:
        return "NEUTRAL"
    if price_up is None:
        return "OI_UP" if doi > 0 else "OI_DOWN"
    if doi > 0:
        return "LONG_BUILDUP" if price_up else "SHORT_BUILDUP"
    # doi < 0
    return "SHORT_COVERING" if price_up else "LONG_UNWINDING"


CLASSIFICATION_EMOJI = {
    "LONG_BUILDUP":    "🟢 Long Build",
    "SHORT_BUILDUP":   "🔴 Short Build",
    "SHORT_COVERING":  "🟡 Short Cover",
    "LONG_UNWINDING":  "🟠 Long Unwind",
    "OI_UP":           "⬆️ OI Up",
    "OI_DOWN":         "⬇️ OI Down",
    "NEUTRAL":         "— Neutral",
}


def build_strike_matrix(
    oi_strikes: list[dict],
    *,
    price_up: Optional[bool] = None,
    spot: float = 0.0,
    min_oi: int = 0,
    top_n: int = 3,
) -> dict:
    """Build the full strike buildup matrix.

    Args:
        oi_strikes: per-strike list from data_access (oi_strikes field of DashboardData).
            Each dict has: strike, call_oi, put_oi, call_doi, put_doi, call_ltp, put_ltp, net_gex.
        price_up: True/False/None — whether spot moved up since session open (for classification).
        spot: current spot price (used to sort strikes relative to spot).
        min_oi: minimum OI threshold to include a strike (index-specific).
        top_n: number of high-conviction strikes to surface.

    Returns:
        {
            "strikes": list[StrikeBuildup],          # all classified strikes, sorted desc by net_velocity
            "top_call_builds": list[StrikeBuildup],  # top-n by call_velocity
            "top_put_builds": list[StrikeBuildup],   # top-n by put_velocity
            "top_conviction": list[StrikeBuildup],   # top-n by net_velocity (overall activity)
            "summary": {
                "long_buildup_count": int,
                "short_buildup_count": int,
                "covering_count": int,
                "unwinding_count": int,
                "dominant_bias": str,                # "BULLISH" / "BEARISH" / "NEUTRAL"
            }
        }
    """
    if oi_strikes is None or (hasattr(oi_strikes, "empty") and oi_strikes.empty) or len(oi_strikes) == 0:
        return _empty_result()

    classified: list[StrikeBuildup] = []
    for row in oi_strikes:
        call_oi = float(row.get("call_oi") or 0)
        put_oi = float(row.get("put_oi") or 0)
        if call_oi + put_oi < min_oi:
            continue
        call_doi = float(row.get("call_doi") or 0)
        put_doi = float(row.get("put_doi") or 0)
        call_cls = classify_buildup(call_doi, price_up)
        put_cls = classify_buildup(put_doi, price_up)
        call_vel = abs(call_doi)
        put_vel = abs(put_doi)
        net_vel = call_vel + put_vel
        if call_vel > put_vel * 1.5:
            dominant = "CALL"
        elif put_vel > call_vel * 1.5:
            dominant = "PUT"
        else:
            dominant = "BALANCED"
        classified.append(StrikeBuildup(
            strike=float(row.get("strike", 0)),
            call_oi=call_oi, put_oi=put_oi,
            call_doi=call_doi, put_doi=put_doi,
            call_classification=call_cls, put_classification=put_cls,
            call_velocity=call_vel, put_velocity=put_vel,
            net_velocity=net_vel, dominant=dominant,
            call_ltp=row.get("call_ltp"), put_ltp=row.get("put_ltp"),
            net_gex=row.get("net_gex"),
        ))

    if not classified:
        return _empty_result()

    classified.sort(key=lambda x: x.net_velocity, reverse=True)

    # Summary counts
    long_b = sum(1 for s in classified if "LONG_BUILDUP" in (s.call_classification, s.put_classification))
    short_b = sum(1 for s in classified if "SHORT_BUILDUP" in (s.call_classification, s.put_classification))
    cover = sum(1 for s in classified if "SHORT_COVERING" in (s.call_classification, s.put_classification))
    unwind = sum(1 for s in classified if "LONG_UNWINDING" in (s.call_classification, s.put_classification))

    if long_b > short_b * 1.5:
        dominant_bias = "BULLISH"
    elif short_b > long_b * 1.5:
        dominant_bias = "BEARISH"
    else:
        dominant_bias = "NEUTRAL"

    top_call = sorted(classified, key=lambda x: x.call_velocity, reverse=True)[:top_n]
    top_put = sorted(classified, key=lambda x: x.put_velocity, reverse=True)[:top_n]
    top_conv = classified[:top_n]  # already sorted by net_velocity

    return {
        "strikes": classified,
        "top_call_builds": top_call,
        "top_put_builds": top_put,
        "top_conviction": top_conv,
        "summary": {
            "long_buildup_count": long_b,
            "short_buildup_count": short_b,
            "covering_count": cover,
            "unwinding_count": unwind,
            "dominant_bias": dominant_bias,
        },
    }


def _empty_result() -> dict:
    return {
        "strikes": [],
        "top_call_builds": [],
        "top_put_builds": [],
        "top_conviction": [],
        "summary": {
            "long_buildup_count": 0,
            "short_buildup_count": 0,
            "covering_count": 0,
            "unwinding_count": 0,
            "dominant_bias": "NEUTRAL",
        },
    }
