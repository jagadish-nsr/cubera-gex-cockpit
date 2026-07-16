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


def oi_weighted_levels(rows, spot: float | None = None,
                       band_pts: float | None = None) -> dict | None:
    """Standing-OI support/resistance near spot — the S/R that empirically HOLDS (docs §4d:
    total-OI-weighted centre rejects price −2.6 bp, robust across all three expiry eras, and
    beats fresh-ΔOI *and* the GEX walls). Support = PUT-OI side, resistance = CALL-OI side,
    within ±``band_pts`` of spot. Returns BOTH the OI-WEIGHTED centre-of-mass (``*_w`` — the
    validated read) AND the single TOP-OI strike (``*_top`` — the dominant wall, for reference;
    'top' did NOT validate but is shown as context). Uses total OI (call_oi/put_oi), not ΔOI.
    None if there are no usable strikes."""
    ks, coi, poi = [], [], []
    for r in rows:
        k = r.get("strike")
        if k is None:
            continue
        k = float(k)
        if spot is not None and band_pts is not None and abs(k - spot) > band_pts:
            continue
        ks.append(k)
        coi.append(float(r.get("call_oi") or 0.0))
        poi.append(float(r.get("put_oi") or 0.0))
    if not ks:
        return None
    ks, coi, poi = np.array(ks), np.array(coi), np.array(poi)

    def _w(w):
        s = w.sum()
        return float((ks * w).sum() / s) if s > 0 else None

    def _top(w):
        return float(ks[int(np.argmax(w))]) if (w > 0).any() else None

    return {"support_w": _w(poi), "support_top": _top(poi),
            "resistance_w": _w(coi), "resistance_top": _top(coi)}


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


def window_footprint(day_df: pd.DataFrame, minutes: float, spot: float | None = None,
                     band_pts: float | None = None, now_hm: int | None = None,
                     min_frac: float = 0.20) -> dict | None:
    """Net **current-expiry** OI build over the TRAILING ``minutes`` window, near spot.

    For each strike, the window's OI change is ``doi_near(latest) − doi_near(ref)`` where
    ``ref`` is the last snapshot at or before ``now − minutes``. Because ``*_doi_near`` is
    the nearest-expiry OI change *vs prev close* (a constant intraday), the prev-close
    cancels and the difference is the **nearest-expiry** (i.e. current-expiry) OI change
    over the window. If the ``*_doi_near`` columns are absent (old CSVs), it falls back to
    differencing the all-expiry OI *levels* (``call_oi``/``put_oi``) and flags ``near=False``.

    Only OI **additions** are counted per strike (unwinds clipped to 0) — so settlement
    *decay* near expiry can't masquerade as directional flow; a purely-unwinding window
    reads "thin". Direction: ``net = put_add − call_add``; the dominant side must hold
    ``≥ min_frac`` of total adds else the label is "balanced". Returns None if < 2 snapshots.

    Returns {put_add, call_add, net, frac, label ('put-writing'|'call-writing'|'balanced'|
    'thin'), near, partial (window not fully filled yet), span_min, n_strikes}.
    """
    if day_df is None or getattr(day_df, "empty", True) or "time_ist" not in day_df.columns:
        return None
    near = {"call_doi_near", "put_doi_near"} <= set(day_df.columns)
    ccol, pcol = ("call_doi_near", "put_doi_near") if near else ("call_oi", "put_oi")
    if ccol not in day_df.columns or pcol not in day_df.columns:
        return None
    times = sorted(str(t) for t in day_df["time_ist"].dropna().unique())
    if len(times) < 2:
        return None

    def _hm(t):
        try:
            h, m = str(t).split(":"); return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return None
    now = now_hm if now_hm is not None else _hm(times[-1])
    latest_t = times[-1]
    prior = [t for t in times if (_hm(t) or 0) <= (now or 0) - minutes]
    ref_t = prior[-1] if prior else times[0]
    if ref_t == latest_t:
        return None
    partial = not prior
    span_min = max(0, (_hm(latest_t) or 0) - (_hm(ref_t) or 0))

    a = day_df[day_df["time_ist"].astype(str) == ref_t].drop_duplicates("strike").set_index("strike")
    b = day_df[day_df["time_ist"].astype(str) == latest_t].drop_duplicates("strike").set_index("strike")

    def _num(frame, k, col):
        try:
            v = pd.to_numeric(frame.loc[k, col], errors="coerce")
            return float(v) if v == v else None
        except (KeyError, TypeError, ValueError):
            return None

    put_add = call_add = 0.0
    n_k = 0
    for k in b.index:
        if k not in a.index:
            continue
        try:
            kf = float(k)
        except (TypeError, ValueError):
            continue
        if spot is not None and band_pts is not None and abs(kf - spot) > band_pts:
            continue
        cb, ca = _num(b, k, ccol), _num(a, k, ccol)
        pb, pa = _num(b, k, pcol), _num(a, k, pcol)
        if cb is not None and ca is not None:
            dc = cb - ca
            if dc > 0:
                call_add += dc
        if pb is not None and pa is not None:
            dp = pb - pa
            if dp > 0:
                put_add += dp
        n_k += 1
    total = put_add + call_add
    net = put_add - call_add
    frac = (net / total) if total > 0 else 0.0
    if total <= 0:
        label = "thin"
    elif frac >= min_frac:
        label = "put-writing"
    elif frac <= -min_frac:
        label = "call-writing"
    else:
        label = "balanced"
    return {"put_add": put_add, "call_add": call_add, "net": net, "frac": frac,
            "label": label, "near": near, "partial": partial, "span_min": span_min,
            "n_strikes": n_k}


def _longest_run(kinds: list) -> tuple[int, int, str | None]:
    """Longest contiguous run of the SAME non-None value in ``kinds``.
    Returns (start_idx, end_idx_inclusive, kind); (−1, −1, None) if none."""
    best = (-1, -1, None)
    i, n = 0, len(kinds)
    while i < n:
        if kinds[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < n and kinds[j + 1] == kinds[i]:
            j += 1
        if (j - i) > (best[1] - best[0]):
            best = (i, j, kinds[i])
        i = j + 1
    return best


def _trailing_run(kinds: list) -> tuple[int, int, str | None]:
    """The run ENDING at the latest interval (the live/ongoing streak). Returns
    (start_idx, end_idx_inclusive, kind); (−1, −1, None) if the last interval is
    neutral/None (i.e. nothing is building right now)."""
    n = len(kinds)
    if n == 0 or kinds[-1] is None:
        return (-1, -1, None)
    e = n - 1
    s = e
    while s - 1 >= 0 and kinds[s - 1] == kinds[e]:
        s -= 1
    return (s, e, kinds[e])


def intraday_buildup_streaks(day_df: pd.DataFrame, side: str, run: int = 3,
                             tol_prem_pct: float = 0.5, min_cum_doi: float = 2000.0,
                             spot: float | None = None, band_pts: float | None = None,
                             only_live: bool = False) -> list[dict]:
    """Per-strike INTRADAY mini build-up streaks for one side ('call'/'put').

    Between consecutive snapshots each interval is classified from (ΔOI step, premium
    %-change step) via the same price+OI matrix: **green** = long build-up (OI↑ &
    premium↑), **red** = writing / short build-up (OI↑ & premium↓); OI-down intervals
    (covering/unwinding) and sub-tolerance premium moves break the run. A strike is
    flagged when a contiguous run of one colour reaches ``run`` (default 3).

    ``only_live=True`` considers only the **trailing** run (the streak still building at
    the latest snapshot) — a "what's accelerating right now" read, dropping stale runs
    that ended earlier in the day. Default (False) reports the day's LONGEST run.
    Absolute per-step ΔOI thresholds don't generalise across capture cadences, so
    direction + run length is the filter; ``tol_prem_pct`` screens premium noise (%) and
    ``min_cum_doi`` requires the run's CUMULATIVE OI build to be non-trivial (drops
    deep-OTM illiquid strikes whose premium %-swings are wild on ~zero OI).

    Returns dicts sorted by run length then |cum ΔOI|: {strike, side, kind (green/red),
    run_len, cum_doi, cum_prem_pct, turnover (run volume ÷ OI = conviction, None if no
    volume column), start_time, end_time, live (run ends at the latest snapshot)}.
    Sign-free, exploratory. Empty if too few snapshots.
    """
    if day_df is None or day_df.empty or "time_ist" not in day_df.columns:
        return []
    oi_col, ltp_col, vol_col = f"{side}_oi", f"{side}_ltp", f"{side}_vol"
    if oi_col not in day_df.columns or ltp_col not in day_df.columns:
        return []
    has_vol = vol_col in day_df.columns
    out = []
    for k, sub in day_df.groupby("strike"):
        k = float(k)
        if spot is not None and band_pts is not None and abs(k - spot) > band_pts:
            continue
        sub = sub.drop_duplicates("time_ist").sort_values("time_ist")
        if len(sub) < run + 1:                     # need run+1 points for `run` intervals
            continue
        times = sub["time_ist"].tolist()
        oi = pd.to_numeric(sub[oi_col], errors="coerce").to_numpy()
        ltp = pd.to_numeric(sub[ltp_col], errors="coerce").to_numpy()
        vol = pd.to_numeric(sub[vol_col], errors="coerce").to_numpy() if has_vol else None
        kinds = []
        for i in range(1, len(sub)):
            doi = oi[i] - oi[i - 1]
            prem_pct = (ltp[i] / ltp[i - 1] - 1.0) * 100.0 if ltp[i - 1] else np.nan
            lab = classify_buildup(doi, prem_pct, tol_prem=tol_prem_pct)
            kinds.append("green" if lab == "long build-up"
                         else "red" if lab == "writing (short build-up)" else None)
        s, e, kind = (_trailing_run(kinds) if only_live else _longest_run(kinds))
        if kind is None or (e - s + 1) < run:
            continue
        # interval i spans snapshots [i, i+1]; run covers snapshots [s, e+1]
        cum_doi = float(oi[e + 1] - oi[s])
        if abs(cum_doi) < min_cum_doi:             # skip illiquid-strike noise
            continue
        cum_prem_pct = float((ltp[e + 1] / ltp[s] - 1.0) * 100.0) if ltp[s] else None
        turnover = None                            # run volume ÷ OI = how hard it traded
        if vol is not None and oi[e + 1] and vol[e + 1] == vol[e + 1] and vol[s] == vol[s]:
            turnover = float((vol[e + 1] - vol[s]) / oi[e + 1])
        out.append({"strike": k, "side": side, "kind": kind, "run_len": e - s + 1,
                    "cum_doi": cum_doi, "cum_prem_pct": cum_prem_pct, "turnover": turnover,
                    "start_time": times[s], "end_time": times[e + 1],
                    "live": (e == len(kinds) - 1)})
    out.sort(key=lambda x: (-x["run_len"], -abs(x["cum_doi"])))
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
