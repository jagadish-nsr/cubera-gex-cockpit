"""Layer 1: the compact 5-minute feature cache over the raw Dhan intraday option matrix.

The raw store (data/journal/dhan_intraday/<store>/<day>.parquet, ~15,750 rows/day) is the ONLY
place the full per-strike cross-section lives. Scanning 30M rows per study would be ruinous —
in compute and in tokens — so we make ONE pass and cache a reduced, sign-free per-(day, 5-min)
table that every study reads instead. ~75 rows/day (~40x smaller).

Per 5-min stamp we store the quantities the intraday suite needs, all SIGN-FREE except net_gex
(whose sign is the unvalidated dealer landmine — studies use |net_gex|):
  net_gex  — dealer GEX (model A) from the real per-strike IV smile (Dhan IV, OTM side per strike)
  spot, atm_iv, dte
  peak_oi_strike — max (callOI+putOI) strike (the pin / magnet)
  tot_call_oi, tot_put_oi  (→ pcr), and 5-min ΔOI aggregates
Time-to-expiry T is recovered calendar-free by inverting BS on the OPEN ATM call (price, spot,
strike, Dhan IV; r=q=0 like the rest of the engine) and held fixed across the day — stable
intraday gamma scaling, no dependence on the NSE weekly-expiry-rule landmine. Validated against
the true-expiry EOD app/gex_history on overlapping days (scripts/validate_intraday_gex.py).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURES_DIR = ROOT / "data" / "journal" / "intraday_features"
GRID_MIN = 5                     # 5-minute base grid; studies look k steps ahead for 15/30/60m
_OPEN_MIN = 9 * 60 + 15          # 09:15 IST


def _implied_T(price, S, K, iv, lo=1e-5, hi=1.0):
    """Back out time-to-expiry from one ATM call (r=q=0). Monotonic in T → bisection."""
    from gex.bs import bs_call_price
    f = lambda T: float(bs_call_price(S, K, T, iv)) - price
    if not (price > 0 and S > 0 and K > 0 and iv > 0):
        return None
    if f(lo) > 0 or f(hi) < 0:
        return None
    for _ in range(80):
        m = 0.5 * (lo + hi)
        if f(m) < 0:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def _grid_stamps(df):
    """The 5-min grid times present in the day (09:15, 09:20, …), in order."""
    def mins(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    times = sorted(df["time_ist"].unique(), key=mins)
    return [t for t in times if (mins(t) - _OPEN_MIN) % GRID_MIN == 0]


def day_features(store: str, day: str):
    """Reduce one raw day to its 5-min feature rows (DataFrame) or None if unusable."""
    from data.dhan import load_intraday_day
    from gex.bs import bs_gamma
    import numpy as np
    import pandas as pd

    df = load_intraday_day(store, day)
    if df is None or df.empty:
        return None
    df = df.copy()
    df["iv"] = pd.to_numeric(df["iv"], errors="coerce") / 100.0     # Dhan IV is in percent
    stamps = _grid_stamps(df)
    if not stamps:
        return None

    # Fix T once from the OPEN ATM call, held across the session (see module docstring).
    T = None
    for st in stamps:
        s0 = df[df["time_ist"] == st]
        spot0 = float(s0["spot"].iloc[0])
        atm = s0.iloc[(s0["strike"] - spot0).abs().argmin()]
        ce = s0[(s0["strike"] == atm["strike"]) & (s0["side"] == "CE")]
        if not ce.empty and ce["iv"].iloc[0] > 0 and ce["close"].iloc[0] > 0:
            T = _implied_T(float(ce["close"].iloc[0]), spot0, float(atm["strike"]),
                           float(ce["iv"].iloc[0]))
            if T:
                break
    if not T:
        return None

    rows = []
    prev_tot = None
    for st in stamps:
        s = df[df["time_ist"] == st]
        spot = float(s["spot"].iloc[0])
        ce = s[s["side"] == "CE"]
        pe = s[s["side"] == "PE"]
        ce_oi = ce.groupby("strike")["oi"].sum()          # dedupe any repeated (strike, side) rows
        pe_oi = pe.groupby("strike")["oi"].sum()
        ce_iv = ce.groupby("strike")["iv"].mean()         # mean skips NaN
        pe_iv = pe.groupby("strike")["iv"].mean()
        strikes = sorted(set(ce_oi.index) | set(pe_oi.index))
        if not strikes:
            continue
        gex = tot_c = tot_p = 0.0
        peak_k, peak_oi = spot, -1.0
        atm_k = min(strikes, key=lambda kk: abs(kk - spot))
        atm_iv = None
        for k in strikes:
            coi = float(ce_oi.get(k, 0.0))
            poi = float(pe_oi.get(k, 0.0))
            tot_c += coi
            tot_p += poi
            if coi + poi > peak_oi:
                peak_oi, peak_k = coi + poi, k
            iv = float(ce_iv.get(k, np.nan)) if k >= spot else float(pe_iv.get(k, np.nan))  # OTM side
            if k == atm_k and iv == iv and iv > 0:
                atm_iv = iv
            if iv == iv and iv > 0:                                    # all offsets are near-money
                gex += bs_gamma(spot, k, T, iv) * (coi - poi)
        gex *= spot * spot * 0.01
        tot = tot_c + tot_p
        d_oi = (tot - prev_tot) if prev_tot is not None else 0.0
        prev_tot = tot
        rows.append({"day": day, "time_ist": st, "spot": spot, "net_gex": gex,
                     "atm_iv": atm_iv, "dte": T * 365.0, "peak_oi_strike": peak_k,
                     "tot_call_oi": tot_c, "tot_put_oi": tot_p, "d_tot_oi": d_oi})
    return pd.DataFrame(rows)


def _cache_path(store: str) -> Path:
    return FEATURES_DIR / f"{store}.parquet"


def build(store: str = "NIFTY", refresh: bool = True, max_new: int | None = None,
          log=print) -> dict:
    """Build/extend the cached 5-min feature table (append-only). ``max_new`` caps how many
    uncached days are computed this call (newest first) so a dashboard call can't trigger a full
    rebuild; a backfill script passes None. Returns a small summary."""
    from data.dhan import available_intraday_days
    import pandas as pd

    path = _cache_path(store)
    have = pd.read_parquet(path) if path.exists() else None
    done = set(have["day"].unique()) if have is not None else set()
    todo = [d for d in available_intraday_days(store) if d not in done]
    if max_new is not None:
        todo = todo[-max_new:]
    if not refresh or not todo:
        return {"store": store, "cached_days": len(done), "new_days": 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    new, n_ok = [], 0
    for i, d in enumerate(todo, 1):
        f = day_features(store, d)
        if f is not None:
            new.append(f)
            n_ok += 1
        if i % 100 == 0:
            log(f"  {store}: {i}/{len(todo)} days processed ({n_ok} ok)")
    if new:
        out = pd.concat(([have] if have is not None else []) + new, ignore_index=True)
        out = out.sort_values(["day", "time_ist"]).reset_index(drop=True)
        out.to_parquet(path, index=False)
    return {"store": store, "cached_days": len(done) + n_ok, "new_days": n_ok}


def load_features(store: str = "NIFTY"):
    """Read the cached 5-min feature table (one row per day×5-min) or None if not built."""
    import pandas as pd
    p = _cache_path(store)
    return pd.read_parquet(p) if p.exists() else None
