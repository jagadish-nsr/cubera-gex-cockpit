"""analytics/pcr_bands.py — Put-Call Ratio broken down by strike proximity to spot.

Pure function: no network, no streamlit. Input is the per_strike list from
data.dhan.per_strike_oi_dhan().

The total PCR tells you the overall option market lean, but PCR *by band* tells
you WHERE the put-writing or call-writing is concentrated:
  - Near ATM (±200 pts for Nifty): the market-maker / institutional hedging band.
  - Mid range (±500 pts): the main OI anchor zone.
  - Far OTM (±1000 pts+): cheap tail-hedges, structural put-buying floor.

Separation guarantee: imports nothing from data or app layers.
"""

from __future__ import annotations


def pcr_by_band(
    per_strike: list[dict],
    spot: float,
    band_pts: list[float] | None = None,
) -> dict[str, dict]:
    """PCR for concentric strike bands around spot.

    Args:
        per_strike: list of per-strike dicts with keys ``strike``, ``call_oi``, ``put_oi``.
                    As produced by ``data.dhan.per_strike_oi_dhan()``.
        spot:       Current underlying level.
        band_pts:   Half-widths of the bands. Defaults to [200, 500, 1000].
                    Each band is INCLUSIVE (spot ± band_pts[i]).

    Returns:
        Dict keyed by band label ("±200", "±500", "±1000") containing:
            {"call_oi": float, "put_oi": float, "pcr": float, "strikes_included": int}
        A band with zero call OI returns pcr=nan.

    Example:
        bands = pcr_by_band(per_strike, spot=24500)
        bands["±200"]["pcr"]  # PCR for 24300–24700 strikes
    """
    if band_pts is None:
        band_pts = [200.0, 500.0, 1000.0]

    result: dict[str, dict] = {}

    for half_width in band_pts:
        label = f"±{int(half_width)}"
        call_oi = put_oi = 0.0
        n = 0
        for row in per_strike:
            k = float(row.get("strike", 0))
            if abs(k - spot) <= half_width:
                call_oi += float(row.get("call_oi") or 0.0)
                put_oi  += float(row.get("put_oi")  or 0.0)
                n += 1
        pcr = (put_oi / call_oi) if call_oi > 0 else float("nan")
        result[label] = {
            "call_oi": call_oi,
            "put_oi":  put_oi,
            "pcr":     pcr,
            "strikes_included": n,
        }

    return result


def pcr_band_label(pcr: float) -> str:
    """Human-readable label for a PCR value (India convention: high PCR = bullish)."""
    if pcr != pcr:  # nan
        return "n/a"
    if pcr >= 1.5:
        return "put-heavy (bullish)"
    if pcr >= 1.2:
        return "mild put-heavy"
    if pcr <= 0.6:
        return "call-heavy (bearish)"
    if pcr <= 0.8:
        return "mild call-heavy"
    return "neutral"
