"""Per-strike GEX, walls, magnet, and regime (SpotGamma convention).

Pure functions over a contracts DataFrame with columns:
    strike (float), type ("C"/"P"), gamma (per-share BS gamma), oi (open interest)

Dollar GEX per strike:
    call_gex[k] = +gamma * OI * contract_size * spot^2 * 0.01   (dealers long call gamma)
    put_gex[k]  = -gamma * OI * contract_size * spot^2 * 0.01   (dealers short put gamma)
    net_gex[k]  = call_gex[k] + put_gex[k]

The spot^2 * 0.01 term scales to dollar dealer hedging per 1% move.
"""

from __future__ import annotations

import pandas as pd

_SUPPORTED_SIGN_MODELS = {"long_call_short_put", "short_call_short_put"}


def _scale(spot: float, contract_size: int) -> float:
    return spot * spot * 0.01 * contract_size


def per_strike_gex(
    contracts: pd.DataFrame,
    spot: float,
    contract_size: int = 100,
    dealer_sign_model: str = "long_call_short_put",
) -> pd.DataFrame:
    """Aggregate contract-level rows into per-strike call/put/net GEX.

    Returns a DataFrame: strike, call_gex, put_gex, net_gex (one row per strike,
    sorted by strike). Fails loud on an unknown sign model rather than silently
    applying the wrong gamma sign (CLAUDE.md #5/#6).
    """
    if dealer_sign_model not in _SUPPORTED_SIGN_MODELS:
        raise ValueError(
            f"Unknown DEALER_SIGN_MODEL {dealer_sign_model!r}; "
            f"supported: {sorted(_SUPPORTED_SIGN_MODELS)}"
        )

    required = {"strike", "type", "gamma", "oi"}
    missing = required - set(contracts.columns)
    if missing:
        raise ValueError(f"contracts missing columns: {sorted(missing)}")

    df = contracts.copy()
    df["type"] = df["type"].str.upper()
    bad = set(df["type"].unique()) - {"C", "P"}
    if bad:
        raise ValueError(f"contract type must be 'C' or 'P'; got {sorted(bad)}")

    scale = _scale(spot, contract_size)
    # dollar GEX magnitude per contract row, times the (optional) expiry weight
    w = df["weight"] if "weight" in df.columns else 1.0
    df["_mag"] = df["gamma"] * df["oi"] * scale * w
    df["_call"] = df["_mag"].where(df["type"] == "C", 0.0)
    df["_put"] = df["_mag"].where(df["type"] == "P", 0.0)

    grouped = df.groupby("strike", as_index=False).agg(
        call_gex=("_call", "sum"),
        put_gex=("_put", "sum"),
    )
    # dealers SHORT put gamma -> negate
    grouped["put_gex"] = -grouped["put_gex"]

    # For Indian markets where dealers are also short calls
    if dealer_sign_model == "short_call_short_put":
        grouped["call_gex"] = -grouped["call_gex"]

    grouped["net_gex"] = grouped["call_gex"] + grouped["put_gex"]
    return grouped.sort_values("strike").reset_index(drop=True)


def call_wall(per_strike: pd.DataFrame) -> float:
    """Strike with the highest positive call GEX (resistance)."""
    return float(per_strike.loc[per_strike["call_gex"].idxmax(), "strike"])


def put_wall(per_strike: pd.DataFrame) -> float:
    """Strike with the largest absolute put GEX (support if above flip)."""
    idx = per_strike["put_gex"].abs().idxmax()
    return float(per_strike.loc[idx, "strike"])


def magnet(per_strike: pd.DataFrame) -> float:
    """Peak-GEX strike: largest absolute net GEX."""
    idx = per_strike["net_gex"].abs().idxmax()
    return float(per_strike.loc[idx, "strike"])


def total_net_gex(per_strike: pd.DataFrame) -> float:
    return float(per_strike["net_gex"].sum())


def regime(per_strike: pd.DataFrame) -> str:
    """POSITIVE (pin/fade) vs NEGATIVE (momentum/amplify) vs FLAT (exactly 0)."""
    total = total_net_gex(per_strike)
    if total > 0:
        return "POSITIVE"
    if total < 0:
        return "NEGATIVE"
    return "FLAT"
