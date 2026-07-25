"""analytics/strikes.py — Strike-level OI analysis (§3.3 of the divergence spec).

Derives support/resistance levels and fresh-money build-up from Dhan option-chain
data. Inputs are the plain list-of-dicts produced by data.dhan.per_strike_oi_dhan()
so these functions are network-free and fully unit-testable.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# §3.3 Strike-level layer helpers
# ---------------------------------------------------------------------------

def max_call_oi_strike(per_strike: list[dict]) -> dict | None:
    """Return the strike with highest total call OI — nearest overhead resistance.

    per_strike: list from per_strike_oi_dhan() — each item has keys
        {strike, call_oi, call_doi, call_vol, put_oi, put_doi, put_vol}.

    Returns {"strike": float, "call_oi": float} or None if list is empty.
    """
    if not per_strike:
        return None
    best = max(per_strike, key=lambda x: x.get("call_oi") or 0)
    return {"strike": best["strike"], "call_oi": best.get("call_oi", 0)}


def max_put_oi_strike(per_strike: list[dict]) -> dict | None:
    """Return the strike with highest total put OI — nearest support below spot.

    Returns {"strike": float, "put_oi": float} or None if list is empty.
    """
    if not per_strike:
        return None
    best = max(per_strike, key=lambda x: x.get("put_oi") or 0)
    return {"strike": best["strike"], "put_oi": best.get("put_oi", 0)}


def top_oi_change_strikes(per_strike: list[dict], n: int = 3) -> list[dict]:
    """Return top-N strikes by absolute total OI change (call_doi + put_doi).

    These show where FRESH money is being committed today — more informative than
    raw OI level (which reflects old stale positions).

    Returns list of dicts sorted descending by total absolute DOI:
        [{"strike": float, "call_doi": float, "put_doi": float,
          "total_abs_doi": float, "direction": "call_build"|"put_build"|"mixed"}, ...]
    """
    if not per_strike:
        return []

    scored = []
    for s in per_strike:
        cd = s.get("call_doi") or 0.0
        pd = s.get("put_doi")  or 0.0
        total_abs = abs(cd) + abs(pd)
        if total_abs == 0:
            continue
        # Direction hint: which side is seeing larger fresh build-up
        if abs(cd) > abs(pd) * 1.5:
            direction = "call_build"
        elif abs(pd) > abs(cd) * 1.5:
            direction = "put_build"
        else:
            direction = "mixed"
        scored.append({
            "strike":       s["strike"],
            "call_doi":     cd,
            "put_doi":      pd,
            "total_abs_doi": total_abs,
            "direction":    direction,
        })

    scored.sort(key=lambda x: x["total_abs_doi"], reverse=True)
    return scored[:n]


def pcr_from_chain(rows: list[dict]) -> dict:
    """Compute Put-Call-Ratio and total OI from raw option chain rows.

    Reuses data.dhan.aggregate_oi_volume_dhan() so there's no duplication.

    Returns:
        {"pcr": float, "total_call_oi": float, "total_put_oi": float,
         "total_call_doi": float, "total_put_doi": float}
    """
    from data.dhan import aggregate_oi_volume_dhan

    agg = aggregate_oi_volume_dhan(rows)
    return {
        "pcr":            agg.get("pcr_oi", float("nan")),
        "total_call_oi":  agg.get("call_oi",  0.0),
        "total_put_oi":   agg.get("put_oi",   0.0),
        "total_call_doi": agg.get("call_doi", 0.0),
        "total_put_doi":  agg.get("put_doi",  0.0),
    }


def combine_divergence_with_strikes(
    bias: dict,
    strike_analysis: dict,
    spot: float,
) -> str:
    """Build a combined narrative merging category bias (§3.1) with strike OI (§3.3).

    bias:             output of compute_category_biases()
    strike_analysis:  dict with keys max_call, max_put, top_doi, pcr (from build_report)
    spot:             current underlying price

    Returns a plain-language string combining both layers into one read.
    """
    lines: list[str] = []

    divergence = bias.get("divergence", 0)
    retail     = bias.get("retail_bias", 0)
    inst       = bias.get("institutional_bias", 0)

    max_call = strike_analysis.get("max_call") or {}
    max_put  = strike_analysis.get("max_put")  or {}
    pcr_val  = strike_analysis.get("pcr", {}).get("pcr", None)

    call_wall = max_call.get("strike")
    put_wall  = max_put.get("strike")

    if call_wall and put_wall:
        call_dist = call_wall - spot
        put_dist  = put_wall  - spot
        lines.append(
            f"Option chain walls: **Call OI wall at {call_wall:,.0f}** "
            f"({call_dist:+,.0f} pts) · **Put OI wall at {put_wall:,.0f}** "
            f"({put_dist:+,.0f} pts)."
        )

    # Combined bias + wall read
    if divergence > 500_000 and inst < 0:
        # Retail long vs FII short
        if put_wall and put_wall < spot:
            lines.append(
                f"**High-conviction setup:** retail crowded long + FII net short "
                f"+ heavy put OI wall below spot ({put_wall:,.0f}) — "
                "if put wall breaks, retail stops may amplify the downside."
            )
        elif call_wall and call_wall > spot:
            lines.append(
                f"Retail long but call wall at {call_wall:,.0f} caps upside — "
                "FII short book may benefit from a squeeze against the wall."
            )
    elif divergence < -500_000 and inst > 0:
        # Retail short vs FII long
        if call_wall and call_wall > spot:
            lines.append(
                f"**Short-squeeze setup:** retail short + FII long "
                f"+ call OI wall at {call_wall:,.0f} overhead — "
                "a break above the call wall could force retail covering."
            )

    if pcr_val is not None and not (isinstance(pcr_val, float) and pcr_val != pcr_val):
        pcr_label = (
            "elevated (put-heavy)" if pcr_val > 1.3
            else "low (call-heavy)" if pcr_val < 0.7
            else "neutral"
        )
        lines.append(f"PCR: **{pcr_val:.2f}** ({pcr_label}).")

    if not lines:
        lines.append("No high-conviction combined signal — individual layers do not reinforce each other today.")

    return "  \n".join(lines)
