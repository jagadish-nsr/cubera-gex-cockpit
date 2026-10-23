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
    expiry_weighting: str = "equal"
    parity_fits: dict = field(default_factory=dict)
    flip_result: FlipResult | None = None
    flags: list[str] = field(default_factory=list)
    per_strike: pd.DataFrame | None = None
    atm_iv: float | None = None
    pcr_oi: float | None = None
    pcr_near: float | None = None


def compute_levels(
    snapshot: OptionsSnapshot,
    contract_size: int = 100,
    grid_range: float = 0.10,
    grid_step: float = 0.0025,
    oi_stale_hours: float = 30.0,
    expiry_weighting: str = "equal",
    weight_halflife_days: float = 5.0,
) -> Levels:
    snapshot.validate()
    flags: list[str] = []
    spot = snapshot.spot

    # 1. Imply the FORWARD per expiry from put-call parity, and derive gamma's
    # rate inputs from it. The forward is robust (it's the x-intercept where
    # C-P=0); the slope-implied *rate* is NOT (fragile for short-dated options).
    # Gamma's d1 needs only the carry c = ln(F/S)/T, which the robust forward
    # gives -> set r=c, q=0 (the e^{-qT} dividend prefactor ~1 for short-dated
    # index options is negligible). Market-implied, but without the fragile rate.
    contracts, parity_fits = attach_parity_rates(snapshot.contracts, spot)
    flagged_expiries = [str(e) for e, f in parity_fits.items() if f.flagged]
    if flagged_expiries:
        flags.append(f"parity fit failed for expiries: {flagged_expiries}")

    contracts = contracts.copy()
    contracts["r"] = np.nan
    contracts["q"] = 0.0
    for exp, fit in parity_fits.items():
        if fit.flagged or not fit.forward:
            continue
        mask = contracts["expiry"] == exp
        T = contracts.loc[mask, "T"].clip(lower=1e-6)
        contracts.loc[mask, "r"] = np.log(fit.forward / spot) / T  # implied carry

    # Sanity check on the FORWARD (robust, T-independent): a forward sitting far
    # from spot means stale/illiquid prices. Replaces the old slope-rate flag.
    bad_fwd = [
        str(exp) for exp, fit in parity_fits.items()
        if not fit.flagged and fit.forward and abs(fit.forward / spot - 1) > 0.03
    ]
    if bad_fwd:
        flags.append(
            f"implied forward >3% from spot for {len(bad_fwd)} expiries "
            "— likely stale/illiquid prices; re-run during market hours"
        )

    # Drop rows whose expiry parity failed (no forward) so we never compute on a
    # bad fit. Fall back gracefully (r=0, forward≈spot) when ALL fits fail — this
    # happens post-close on expiry day (T≤0) or when prices are all stale/zero.
    # A loud flag is added so the user knows not to trade on this.
    usable = contracts[contracts["r"].notna()].copy()
    if usable.empty:
        flags.append(
            "ALL parity fits failed (T≤0 or stale prices) — using r=0/F=spot fallback. "
            "This is expected post-close on expiry day. Reload during market hours for live GEX."
        )
        # Fall back: assign r=0 for all contracts so downstream code can proceed.
        # gamma is computed from IV only (rate barely matters for short-dated options).
        usable = contracts.copy()
        usable["r"] = 0.0
        usable["q"] = 0.0
        if usable.empty:
            raise ValueError(
                "no expiry produced a usable parity forward — cannot compute GEX. "
                "Data is likely entirely empty. Try switching to 'weekly' or 'all' window."
            )

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

    # 3b. Expiry weight per contract (near-dated gamma weighted up). Explicit +
    # logged. Single-expiry chains get a uniform weight -> no effect on levels.
    from gex.weighting import expiry_weights

    days = usable["T"].to_numpy(float) * 365.25
    usable["weight"] = expiry_weights(
        days, scheme=expiry_weighting, halflife_days=weight_halflife_days
    )
    if expiry_weighting != "equal":
        flags.append(
            f"expiry weighting = {expiry_weighting}"
            + (f" (half-life {weight_halflife_days:g}d)" if expiry_weighting == "exp" else "")
            + " — near-dated walls weighted up"
        )

    # 4. Per-strike GEX, walls, magnet, regime.
    per_strike = core.per_strike_gex(
        usable[["strike", "type", "gamma", "oi", "weight"]],
        spot=spot,
        contract_size=contract_size,
        dealer_sign_model=snapshot.dealer_sign_model,
    )

    # 5. Gamma flip as a zero-crossing profile (recomputes gamma over the grid).
    flip_res = gamma_flip_profile(
        usable[["strike", "type", "oi", "iv", "T", "r", "q", "weight"]],
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

    # 8. Compute authentic ATM IV and PCR from contract set
    calc_atm_iv = None
    calc_pcr_oi = None
    calc_pcr_near = None
    try:
        c_df = snapshot.contracts
        if c_df is not None and not c_df.empty:
            c_oi = float(c_df[c_df["type"] == "C"]["oi"].sum())
            p_oi = float(c_df[c_df["type"] == "P"]["oi"].sum())
            if c_oi > 0:
                calc_pcr_oi = round(p_oi / c_oi, 4)

            # Near-spot PCR (nearest expiry, 21 closest strikes) — what traders
            # actually watch. Full-chain PCR is dominated by far-OTM call junk.
            near_exp = c_df["expiry"].min()
            near_df = c_df[c_df["expiry"] == near_exp].copy()
            near_strikes = sorted(near_df["strike"].unique(), key=lambda k: abs(float(k) - spot))[:21]
            band = near_df[near_df["strike"].isin(near_strikes)]
            band_c = float(band[band["type"] == "C"]["oi"].sum())
            band_p = float(band[band["type"] == "P"]["oi"].sum())
            if band_c > 0:
                calc_pcr_near = round(band_p / band_c, 4)

            c_valid = c_df.dropna(subset=["iv"])
            c_valid = c_valid[c_valid["iv"] > 0.01]
            if not c_valid.empty:
                near_exp_iv = c_valid["expiry"].min()
                near_iv_df = c_valid[c_valid["expiry"] == near_exp_iv].copy()
                near_iv_df["_dist"] = (near_iv_df["strike"] - spot).abs()
                atm_rows = near_iv_df.nsmallest(4, "_dist")
                iv_s = atm_rows["iv"].dropna()
                if not iv_s.empty:
                    calc_atm_iv = round(float(iv_s.mean()), 4)
    except Exception:
        pass

    sign_model = snapshot.dealer_sign_model
    return Levels(
        spot=spot,
        call_wall=core.call_wall(per_strike),
        put_wall=core.put_wall(per_strike),
        magnet=core.magnet(per_strike),
        flip=flip_res.flip,
        max_pain=mp,
        total_net_gex=core.writer_imbalance_net(per_strike, sign_model),
        regime=core.regime(per_strike, sign_model),
        dealer_sign_model=snapshot.dealer_sign_model,
        expiry_window=snapshot.expiry_window,
        oi_timestamp=snapshot.oi_timestamp,
        oi_stale=oi_stale,
        source=snapshot.source,
        expiry_weighting=expiry_weighting,
        parity_fits=parity_fits,
        flip_result=flip_res,
        flags=flags,
        per_strike=per_strike,
        atm_iv=calc_atm_iv,
        pcr_oi=calc_pcr_oi,
        pcr_near=calc_pcr_near,
    )


def _fill_missing_iv(contracts: pd.DataFrame, spot: float) -> pd.DataFrame:
    """Back out IV from mid price for rows where iv is NaN (calls and puts).

    Uses put-call parity's r/q. For puts we convert to the synthetic call price
    via parity (C = P + DF*(F - K)) and invert that, since bs invert is on calls.
    """
    from gex.bs import implied_vol_call_vec

    df = contracts.copy()
    mask = df["iv"].isna() & df["mid"].notna()
    if not mask.any():
        return df

    # Extract arrays for the missing subset
    r = df.loc[mask, "r"].to_numpy(dtype=float)
    q = df.loc[mask, "q"].to_numpy(dtype=float)
    T = df.loc[mask, "T"].to_numpy(dtype=float)
    K = df.loc[mask, "strike"].to_numpy(dtype=float)
    mid = df.loc[mask, "mid"].to_numpy(dtype=float)
    is_call = (df.loc[mask, "type"].str.upper() == "C").to_numpy()

    DF = np.exp(-r * T)
    F = spot * np.exp((r - q) * T)

    # Convert put mid prices to synthetic call prices using put-call parity
    call_prices = np.where(is_call, mid, mid + DF * (F - K))

    # Compute vectorized IV
    iv_arr = implied_vol_call_vec(call_prices, np.full_like(K, spot), K, T, r, q)
    df.loc[mask, "iv"] = iv_arr

    return df
