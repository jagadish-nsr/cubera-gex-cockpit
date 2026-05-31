"""Orchestrator: OptionsSnapshot -> Levels.

Single source of truth for gamma: we RECOMPUTE per-contract gamma from IV at the
current spot (gex.bs.bs_gamma) using parity-implied r/q, rather than trusting the
vendor's static gamma. Vendor gamma is kept only as an optional cross-check.
Every assumption used is carried onto the Levels object so cli.py can log it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from data.parity import attach_parity_rates
from data.schema import OptionsSnapshot
from gex import core
from gex.bs import bs_gamma
from gex.flip import FlipResult, gamma_flip_profile


@dataclass
class Levels:
    spot: float
    call_wall: float
    put_wall: float
    magnet: float
    flip: float | None
    max_pain: float
    total_net_gex: float
    regime: str
    # provenance / assumptions (logged on every output)
    dealer_sign_model: str
    expiry_window: str
    oi_timestamp: object
    oi_stale: bool
    source: str
    parity_fits: dict = field(default_factory=dict)
    flip_result: FlipResult | None = None
    flags: list[str] = field(default_factory=list)
    per_strike: pd.DataFrame | None = None


def compute_levels(
    snapshot: OptionsSnapshot,
    contract_size: int = 100,
    grid_range: float = 0.10,
    grid_step: float = 0.0025,
    oi_stale_hours: float = 30.0,
) -> Levels:
    snapshot.validate()
    flags: list[str] = []
    spot = snapshot.spot

    # 1. Imply r, q per expiry from put-call parity (no hard-coded macro inputs).
    contracts, parity_fits = attach_parity_rates(snapshot.contracts, spot)
    flagged_expiries = [str(e) for e, f in parity_fits.items() if f.flagged]
    if flagged_expiries:
        flags.append(f"parity fit failed for expiries: {flagged_expiries}")

    # Loud sanity check: non-physical implied rates (|r| or |q| > 50%) usually
    # mean stale/unsynchronised prices (e.g. weekend EOD closes) corrupting the
    # parity slope. Surface it rather than presenting absurd rates as clean.
    implausible = [
        str(e) for e, f in parity_fits.items()
        if not f.flagged and (abs(f.r) > 0.5 or abs(f.q) > 0.5)
    ]
    if implausible:
        flags.append(
            f"IMPLAUSIBLE parity rates (>50%) for {len(implausible)} expiries "
            "— likely stale/illiquid prices; re-run during market hours with live quotes"
        )

    # Drop rows whose expiry parity failed (r/q NaN) so we never compute on a
    # silently-wrong rate. Fail loud if that leaves nothing.
    usable = contracts[contracts["r"].notna()].copy()
    if usable.empty:
        raise ValueError("no expiry produced a usable parity fit — cannot compute GEX")

    # 2. Back out IV from mid price where the vendor omitted it (flag the count).
    n_missing_iv = int(usable["iv"].isna().sum())
    if n_missing_iv:
        flags.append(f"{n_missing_iv} contracts missing vendor IV (derive from price)")
        usable = _fill_missing_iv(usable, spot)
        still = int(usable["iv"].isna().sum())
        if still:
            flags.append(f"{still} contracts still lack IV after inversion — excluded")
            usable = usable[usable["iv"].notna()].copy()

    # 3. Recompute gamma at current spot from IV (single source of truth).
    usable["gamma"] = bs_gamma(
        spot,
        usable["strike"].to_numpy(float),
        usable["T"].to_numpy(float),
        usable["iv"].to_numpy(float),
        usable["r"].to_numpy(float),
        usable["q"].to_numpy(float),
    )

    # 4. Per-strike GEX, walls, magnet, regime.
    per_strike = core.per_strike_gex(
        usable[["strike", "type", "gamma", "oi"]],
        spot=spot,
        contract_size=contract_size,
        dealer_sign_model=snapshot.dealer_sign_model,
    )

    # 5. Gamma flip as a zero-crossing profile (recomputes gamma over the grid).
    flip_res = gamma_flip_profile(
        usable[["strike", "type", "oi", "iv", "T", "r", "q"]],
        spot=spot,
        contract_size=contract_size,
        grid_range=grid_range,
        grid_step=grid_step,
        dealer_sign_model=snapshot.dealer_sign_model,
    )
    if flip_res.flagged:
        flags.append(f"flip: {flip_res.note}")

    # 6. Max pain (over OI only).
    from gex.maxpain import max_pain

    mp = max_pain(usable[["strike", "type", "oi"]], contract_size=contract_size)

    # 7. OI staleness.
    oi_stale = snapshot.oi_is_stale(oi_stale_hours)
    if oi_stale:
        flags.append(
            f"OI is {snapshot.oi_age_hours():.1f}h old (> {oi_stale_hours}h threshold) — stale"
        )

    return Levels(
        spot=spot,
        call_wall=core.call_wall(per_strike),
        put_wall=core.put_wall(per_strike),
        magnet=core.magnet(per_strike),
        flip=flip_res.flip,
        max_pain=mp,
        total_net_gex=core.total_net_gex(per_strike),
        regime=core.regime(per_strike),
        dealer_sign_model=snapshot.dealer_sign_model,
        expiry_window=snapshot.expiry_window,
        oi_timestamp=snapshot.oi_timestamp,
        oi_stale=oi_stale,
        source=snapshot.source,
        parity_fits=parity_fits,
        flip_result=flip_res,
        flags=flags,
        per_strike=per_strike,
    )


def _fill_missing_iv(contracts: pd.DataFrame, spot: float) -> pd.DataFrame:
    """Back out IV from mid price for rows where iv is NaN (calls and puts).

    Uses put-call parity's r/q. For puts we convert to the synthetic call price
    via parity (C = P + DF*(F - K)) and invert that, since bs invert is on calls.
    """
    from gex.bs import implied_vol_call

    df = contracts.copy()
    for idx in df.index[df["iv"].isna()]:
        row = df.loc[idx]
        if not np.isfinite(row.get("mid", np.nan)):
            continue
        r, q, T, K = row["r"], row["q"], row["T"], row["strike"]
        DF = np.exp(-r * T)
        F = spot * np.exp((r - q) * T)
        if row["type"].upper() == "C":
            call_price = row["mid"]
        else:
            call_price = row["mid"] + DF * (F - K)  # put -> synthetic call
        iv = implied_vol_call(call_price, spot, K, T, r, q)
        if np.isfinite(iv):
            df.at[idx, "iv"] = iv
    return df
