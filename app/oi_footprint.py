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
