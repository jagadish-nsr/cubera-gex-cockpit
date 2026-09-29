"""analytics/max_pain.py — Max pain (option writer P&L minimisation) calculation.

Pure function: no network, no streamlit. Input is the per-strike OI list from
data.dhan.per_strike_oi_dhan().

Max pain theory: option writers (who are short options) collectively have the most
profit when the underlying expires AT the strike where total payout to option
BUYERS is minimised. The max pain level is that strike.

Methodology (standard):
  For each candidate expiry strike K_expiry:
    call_pain(K_expiry) = Σ_K [max(K - K_expiry, 0) × call_OI(K)]   (calls expire ITM above K_expiry)
    put_pain(K_expiry)  = Σ_K [max(K_expiry - K, 0) × put_OI(K)]    (puts expire ITM below K_expiry)
    total_pain(K_expiry) = call_pain + put_pain

  max_pain = K_expiry that MINIMISES total_pain.

Note: multiplying by lot_size converts to rupee pain, but the minimiser is
the same strike regardless — lot_size scales all rows equally and cancels.
This implementation returns the minimiser strike + the per-strike pain table.

Reference: standard option-market-maker max-pain calculation, commonly used in
NSE Nifty/BankNifty weekly expiry analysis.
"""

from __future__ import annotations


def max_pain(
    per_strike: list[dict],
    lot_size: int = 1,
) -> dict | None:
    """Find the max pain strike (minimises total option writer payout).

    Args:
        per_strike: list of per-strike dicts with keys ``strike``, ``call_oi``,
                    ``put_oi``. As produced by ``data.dhan.per_strike_oi_dhan()``.
        lot_size:   Contract lot size. Used only to scale the pain table for display;
                    does NOT change which strike is the minimiser.

    Returns:
        {
            "max_pain_strike":  float,          # strike minimising total payout
            "total_pain":       float,          # payout at that strike (lots × pts)
            "pain_table":       list[dict],     # per-candidate-strike breakdown
                                                #   [{strike, call_pain, put_pain, total_pain}]
        }
        or None if per_strike is empty.
    """
    if not per_strike:
        return None

    strikes = sorted({float(r["strike"]) for r in per_strike})
    oi_map: dict[float, dict] = {}
    for r in per_strike:
        k = float(r["strike"])
        oi_map[k] = {
            "call_oi": float(r.get("call_oi") or 0.0),
            "put_oi":  float(r.get("put_oi")  or 0.0),
        }

    pain_table: list[dict] = []
    for k_exp in strikes:
        call_pain = sum(
            max(k - k_exp, 0.0) * oi_map.get(k, {}).get("call_oi", 0.0)
            for k in strikes
        ) * lot_size
        put_pain = sum(
            max(k_exp - k, 0.0) * oi_map.get(k, {}).get("put_oi", 0.0)
            for k in strikes
        ) * lot_size
        pain_table.append({
            "strike":     k_exp,
            "call_pain":  call_pain,
            "put_pain":   put_pain,
            "total_pain": call_pain + put_pain,
        })

    minimiser = min(pain_table, key=lambda x: x["total_pain"])
    return {
        "max_pain_strike": minimiser["strike"],
        "total_pain":      minimiser["total_pain"],
        "pain_table":      pain_table,
    }


def max_pain_distance(max_pain_strike: float, spot: float) -> float:
    """Distance from spot to max pain level (positive = above spot)."""
    return max_pain_strike - spot
