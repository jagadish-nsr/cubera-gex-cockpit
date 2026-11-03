"""Per-strike GEX for Nifty book balance.

One convention, ``short_call_short_put``. Both legs are stored negative
(gamma sitting in the written book). The signed total used for regime is
call magnitude minus put magnitude, the same zero-crossing as ``gex.flip``.

    magnitude  = gamma * OI * lot_size * spot^2 * 0.01
    call_gex   = -magnitude
    put_gex    = -magnitude
    imbalance  = call_magnitude - put_magnitude

``spot^2 * 0.01`` is the rupee hedge for a 1% move when OI is in lots and
``lot_size`` is the NSE multiplier. It is not a dollar SPX print.

POSITIVE means the call book is larger. NEGATIVE means the put book is larger.
Neither word means a dealer will pin or chase the tape.
"""

from __future__ import annotations

import pandas as pd

SIGN_MODEL = "short_call_short_put"


def _require_sign(dealer_sign_model: str) -> None:
    """Fail loud on any second convention. This tree does not switch models."""
    if dealer_sign_model != SIGN_MODEL:
        raise ValueError(
            f"GEX convention is {SIGN_MODEL!r} only "
            "(call-gamma magnitude minus put-gamma magnitude). "
            f"Got {dealer_sign_model!r}."
        )


def _scale(spot: float, contract_size: int) -> float:
    return spot * spot * 0.01 * contract_size


def per_strike_gex(
    contracts: pd.DataFrame,
    spot: float,
    contract_size: int,
    dealer_sign_model: str = SIGN_MODEL,
) -> pd.DataFrame:
    """Aggregate contract rows into per-strike call/put/net GEX.

    ``contract_size`` is the index lot. Returns strike, call_gex, put_gex,
    net_gex, one row per strike. Both legs are negative.
    """
    _require_sign(dealer_sign_model)

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
    grouped["call_gex"] = -grouped["call_gex"]
    grouped["put_gex"] = -grouped["put_gex"]
    grouped["net_gex"] = grouped["call_gex"] + grouped["put_gex"]
    return grouped.sort_values("strike").reset_index(drop=True)


def call_wall(per_strike: pd.DataFrame) -> float:
    """Strike with the largest call-gamma magnitude."""
    idx = per_strike["call_gex"].abs().idxmax()
    return float(per_strike.loc[idx, "strike"])


def put_wall(per_strike: pd.DataFrame) -> float:
    """Strike with the largest put-gamma magnitude."""
    idx = per_strike["put_gex"].abs().idxmax()
    return float(per_strike.loc[idx, "strike"])


def magnet(per_strike: pd.DataFrame) -> float:
    """Peak-GEX strike: largest absolute net GEX."""
    idx = per_strike["net_gex"].abs().idxmax()
    return float(per_strike.loc[idx, "strike"])


# Strikes this many steps from spot still count as the near book.
# 16 × 50 = 800 Nifty points, 16 × 100 = 1,600 for Bank Nifty and Sensex.
NEAR_STRIKES = 16
# A far strike is dust when the near band holds under this fraction of the
# chain's peak. A live wall inside the band is never dust.
_FAR_DUST_FRACTION = 0.001


def _band_peak(per_strike: pd.DataFrame, column: str, spot: float, step: float) -> float:
    band = float(step) * NEAR_STRIKES
    near = per_strike[(per_strike["strike"] - float(spot)).abs() <= band + 1e-6]
    if near.empty:
        return 0.0
    return float(near[column].abs().max())


def _far_dust(strike: float, near_peak: float, chain_peak: float, spot: float, step: float) -> bool:
    """True when the named strike sits outside the near band and the near band is empty of gamma."""
    if abs(float(strike) - float(spot)) <= float(step) * NEAR_STRIKES:
        return False
    if chain_peak <= 0:
        return True
    return near_peak < _FAR_DUST_FRACTION * chain_peak


def live_walls(per_strike: pd.DataFrame, spot: float, step: float) -> dict:
    """Walls for the live book.

    The wall is still the largest magnitude on the chain. When that strike is
    far from spot and the near band's gamma is a rounding leftover, no strike
    is named. A far leftover is not a wall.
    """
    call_strike = call_wall(per_strike)
    put_strike = put_wall(per_strike)
    magnet_strike = magnet(per_strike)
    call_dust = _far_dust(
        call_strike, _band_peak(per_strike, "call_gex", spot, step),
        float(per_strike["call_gex"].abs().max()), spot, step,
    )
    put_dust = _far_dust(
        put_strike, _band_peak(per_strike, "put_gex", spot, step),
        float(per_strike["put_gex"].abs().max()), spot, step,
    )
    magnet_dust = _far_dust(
        magnet_strike, _band_peak(per_strike, "net_gex", spot, step),
        float(per_strike["net_gex"].abs().max()), spot, step,
    )
    expired = call_dust and put_dust and magnet_dust
    if expired:
        return {
            "call_wall": None,
            "put_wall": None,
            "magnet": None,
            "near_book_expired": True,
        }
    return {
        "call_wall": None if call_dust else call_strike,
        "put_wall": None if put_dust else put_strike,
        "magnet": None if magnet_dust else magnet_strike,
        "near_book_expired": False,
    }


def total_net_gex(per_strike: pd.DataFrame) -> float:
    return float(per_strike["net_gex"].sum())


def writer_imbalance_net(
    per_strike: pd.DataFrame,
    dealer_sign_model: str = SIGN_MODEL,
) -> float:
    """Call magnitude minus put magnitude. Agrees with the flip zero-crossing.

    The stored ``net_gex`` sum is always ≤ 0 because both legs are negative.
    Do not classify regime from that sum.
    """
    _require_sign(dealer_sign_model)
    return float((-per_strike["call_gex"] + per_strike["put_gex"]).sum())


def regime(
    per_strike: pd.DataFrame,
    dealer_sign_model: str | None = None,
) -> str:
    """CALL-HEAVY (POSITIVE) vs PUT-HEAVY (NEGATIVE) vs FLAT (exactly 0)."""
    total = writer_imbalance_net(per_strike, dealer_sign_model or SIGN_MODEL)
    if total > 0:
        return "POSITIVE"
    if total < 0:
        return "NEGATIVE"
    return "FLAT"
