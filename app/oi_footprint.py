"""Per-strike ΔOI positioning footprint — where big players are BUILDING, and how
that build DRIFTS through the day. The finer-grained companion to the magnet drift.

The magnet is where dealer gamma (the *stock* of positioning) concentrates. Per-strike
ΔOI is the *flow*: which strikes are gaining OI right now. The standard (exploratory)
read: **put OI building = support forming** (writers defend that strike), **call OI
building = resistance forming**. So:

  support_build    = put-ΔOI-weighted strike   (where support is being laid)
  resistance_build = call-ΔOI-weighted strike  (where resistance is being laid)
  net_skew         = total put-adds − total call-adds  (>0 = put-writing dominant)

Tracking support_build / resistance_build across a day's snapshots gives the
intraday DRIFT (are they rolling support up = bullish repositioning?). This is raw
OI, so it's free of the dealer-*gamma*-sign landmine — but the "put-build=support"
mapping is itself a heuristic. Exploratory, decision-support only. Pure functions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def build_footprint(rows, spot: float | None = None, band_pts: float | None = None) -> dict | None:
    """Point-in-time positioning footprint from one snapshot's per-strike ΔOI rows
    (dicts/records with ``strike``, ``call_doi``, ``put_doi``). Optionally restrict
    to strikes within ``band_pts`` of ``spot`` (far-OTM ΔOI is noise). Returns None
    if there are no usable strikes."""
    ks, cd, pdoi = [], [], []
    for r in rows:
        k = r.get("strike")
        if k is None:
            continue
        k = float(k)
        if spot is not None and band_pts is not None and abs(k - spot) > band_pts:
            continue
        ks.append(k)
        cd.append(float(r.get("call_doi") or 0.0))
        pdoi.append(float(r.get("put_doi") or 0.0))
    if not ks:
        return None
    ks, cd, pdoi = np.array(ks), np.array(cd), np.array(pdoi)
    put_add, call_add = pdoi.clip(min=0), cd.clip(min=0)   # OI ADDITIONS only

    def wavg(w):
        s = w.sum()
        return float((ks * w).sum() / s) if s > 0 else None

    return {
        "support_build": wavg(put_add),                    # put-ΔOI-weighted strike
        "resistance_build": wavg(call_add),                # call-ΔOI-weighted strike
        "peak_put_strike": float(ks[int(np.argmax(pdoi))]),
        "peak_call_strike": float(ks[int(np.argmax(cd))]),
        "put_add": float(put_add.sum()),
        "call_add": float(call_add.sum()),
        "net_skew": float(put_add.sum() - call_add.sum()),  # >0 => put-writing dominant
    }


# Build-up type from (ΔOI direction, premium direction) — the standard price+OI matrix.
_BUILDUP = {
    ("up", "down"): "writing (short build-up)",   # OI up, premium down
    ("up", "up"): "long build-up",                # OI up, premium up
    ("down", "up"): "short covering",             # OI down, premium up
    ("down", "down"): "long unwinding",           # OI down, premium down
}


def classify_buildup(doi, dprem, tol_prem: float = 0.0) -> str | None:
    """Classify an option strike's build-up from its OI change and premium change
    (the price+OI matrix). Volume is intensity, not a classifier, so it's not used
    here. Returns None if either input is missing; 'flat' if premium barely moved."""
    if doi is None or dprem is None or doi != doi or dprem != dprem:
        return None
    od = "up" if doi > 0 else "down" if doi < 0 else "flat"
    pd_ = "up" if dprem > tol_prem else "down" if dprem < -tol_prem else "flat"
    if od == "flat" or pd_ == "flat":
        return "no clear build"
    return _BUILDUP[(od, pd_)]


def top_builds(rows, side: str, spot: float | None = None,
               band_pts: float | None = None, top_n: int = 6) -> list[dict]:
    """Top ``top_n`` strikes by OI ADDITION for one side ('call' or 'put') from a
    snapshot's per-strike rows. Positive ΔOI only (builds, not unwinds), banded to
    ±band_pts of spot, sorted by ΔOI descending. Each dict carries strike, doi, ltp,
    vol so the caller can attach a premium-change build-up tag. It also passes through
    the nearest-expiry OI-change (``doi_near``) and premium-change (``dprem``), both vs
    prev close, when present — the aligned inputs for an EXACT build-up tag."""
    out = []
    for r in rows:
        k = r.get("strike")
        if k is None:
            continue
        k = float(k)
        if spot is not None and band_pts is not None and abs(k - spot) > band_pts:
            continue
        doi = float(r.get(f"{side}_doi") or 0.0)
        if doi <= 0:
            continue
        out.append({"strike": k, "doi": doi,
                    "ltp": r.get(f"{side}_ltp"), "vol": r.get(f"{side}_vol"),
                    "doi_near": r.get(f"{side}_doi_near"), "dprem": r.get(f"{side}_dprem")})
    out.sort(key=lambda x: -x["doi"])
    return out[:top_n]


def intraday_premium_change(day_df: pd.DataFrame) -> dict:
    """{strike: {'call': Δprem, 'put': Δprem}} from the day's FIRST vs LAST snapshot
    premiums (same intraday window). Empty if fewer than 2 snapshots. Lets the
    build-up classifier see whether each strike's option got richer or cheaper."""
    if day_df is None or day_df.empty or "time_ist" not in day_df.columns:
        return {}
    times = sorted(day_df["time_ist"].unique())
    if len(times) < 2:
        return {}
    first = day_df[day_df["time_ist"] == times[0]].drop_duplicates("strike").set_index("strike")
    last = day_df[day_df["time_ist"] == times[-1]].drop_duplicates("strike").set_index("strike")

    def dp(k, col):
        try:
            a, b = float(last.loc[k, col]), float(first.loc[k, col])
            return a - b if a == a and b == b else None
        except (KeyError, TypeError, ValueError):
            return None

    out = {}
    for k in last.index:
        if k in first.index:
            out[float(k)] = {"call": dp(k, "call_ltp"), "put": dp(k, "put_ltp")}
    return out


def footprint_series(day_df: pd.DataFrame, band_frac: float = 0.03):
    """Per-snapshot (time_ist, support_build, resistance_build, net_skew) over one
    day's oi_strikes frame, restricting each snapshot to ±``band_frac`` of its spot.
    Sorted by time. Snapshots with no put/call adds are skipped."""
    if day_df is None or day_df.empty or "time_ist" not in day_df.columns:
        return []
    out = []
    for t in sorted(day_df["time_ist"].unique()):
        snap = day_df[day_df["time_ist"] == t]
        spot = float(pd.to_numeric(snap["spot"], errors="coerce").iloc[0])
        fp = build_footprint(snap.to_dict("records"), spot=spot, band_pts=spot * band_frac)
        if fp and fp["support_build"] is not None and fp["resistance_build"] is not None:
            out.append((t, fp["support_build"], fp["resistance_build"], fp["net_skew"]))
    return out
