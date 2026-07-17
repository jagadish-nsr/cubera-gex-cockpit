"""Gamma flip (zero-gamma) as a price-profile zero-crossing — NOT a static strike.

Approach (gex-math skill, BUILD_PLAN.md §3):
  1. Hold each contract's market IV, K, T, and parity-implied r/q fixed
     (sticky-strike assumption -- LOGGED on output).
  2. Build a spot grid S' in spot * [1-range .. 1+range] at fixed steps.
  3. At each S', RECOMPUTE every contract's BS gamma (gex.bs.bs_gamma), apply
     the dealer sign, and sum to total_GEX(S').
  4. The flip = price where the summed curve crosses zero (linear interpolation
     between bracketing grid points).

Fail loud: no crossing in range -> flip=None + flag. Multiple crossings ->
return the one nearest spot, flag the rest. Never fabricate a level.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from gex.bs import bs_gamma


@dataclass
class FlipResult:
    flip: float | None
    profile: pd.DataFrame            # columns: spot, total_gex
    crossings: list[float] = field(default_factory=list)
    flagged: bool = False
    note: str = ""


def gamma_flip_profile(
    contracts: pd.DataFrame,
    spot: float,
    contract_size: int = 100,
    grid_range: float = 0.10,
    grid_step: float = 0.0025,
    dealer_sign_model: str = "long_call_short_put",
) -> FlipResult:
    """contracts columns required: strike, type ("C"/"P"), oi, iv, T, r, q.

    iv = per-contract implied vol, T = years to expiry, r/q = parity-implied
    rate/div for that contract's expiry.
    """
    # Support long_call_short_put (US) and short_call_short_put (India)
    if dealer_sign_model not in {"long_call_short_put", "short_call_short_put"}:
        raise ValueError(f"Unsupported sign model {dealer_sign_model!r}")
    required = {"strike", "type", "oi", "iv", "T", "r", "q"}
    missing = required - set(contracts.columns)
    if missing:
        raise ValueError(f"contracts missing columns for flip: {sorted(missing)}")

    df = contracts.copy()
    df["type"] = df["type"].str.upper()
    t = df["type"].to_numpy()

    K = df["strike"].to_numpy(dtype=float)
    T = df["T"].to_numpy(dtype=float)
    iv = df["iv"].to_numpy(dtype=float)
    oi = df["oi"].to_numpy(dtype=float)
    r = df["r"].to_numpy(dtype=float)
    q = df["q"].to_numpy(dtype=float)
    # optional expiry weight (applied identically at every hypothetical spot)
    weight = df["weight"].to_numpy(dtype=float) if "weight" in df.columns else 1.0

    n_steps = int(round(2 * grid_range / grid_step)) + 1
    grid = spot * (1 - grid_range + grid_step * np.arange(n_steps))

    totals = np.empty(n_steps)
    for i, s_hyp in enumerate(grid):
        g = bs_gamma(s_hyp, K, T, iv, r, q)
        if dealer_sign_model == "long_call_short_put":
            # long call (+1), short put (-1)
            sign = np.where(df["type"].to_numpy() == "C", 1.0, -1.0)
        else: # short_call_short_put
            # short call (-1), short put (-1)
            sign = -1.0
        gex = sign * g * oi * weight * contract_size * s_hyp * s_hyp * 0.01
        totals[i] = gex.sum()

    profile = pd.DataFrame({"spot": grid, "total_gex": totals})

    # Find sign changes between adjacent grid points; linear-interpolate the cross.
    crossings: list[float] = []
    for i in range(n_steps - 1):
        y0, y1 = totals[i], totals[i + 1]
        if y0 == 0.0:
            crossings.append(float(grid[i]))
        elif y0 * y1 < 0:
            x0, x1 = grid[i], grid[i + 1]
            crossings.append(float(x0 - y0 * (x1 - x0) / (y1 - y0)))
    if totals[-1] == 0.0:
        crossings.append(float(grid[-1]))

    if not crossings:
        return FlipResult(
            flip=None,
            profile=profile,
            crossings=[],
            flagged=True,
            note=(
                "Aggregate GEX never crosses zero within "
                f"+/-{grid_range:.0%} of spot - no gamma flip in range (NOT fabricated)."
            ),
        )

    nearest = min(crossings, key=lambda x: abs(x - spot))
    flagged = len(crossings) > 1
    note = (
        f"Multiple zero-crossings {[round(c, 2) for c in crossings]}; "
        f"returning the one nearest spot ({nearest:.2f})."
        if flagged
        else ""
    )
    return FlipResult(
        flip=nearest, profile=profile, crossings=crossings, flagged=flagged, note=note
    )
