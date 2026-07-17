"""Shared 1-minute gamma-blast primitives (single source for the 1-min scripts).

Loads a day's per-strike 1-min premium/OI arrays from the Dhan store, detects each FIRST
near-money crossing (parity: CE≈PE at the cross), and scores the blast-score trajectory
minute-by-minute from the cross. Used by both ``analyze_blast_timing.py`` (measurement) and
``backtest_gamma_blast.py`` (the [B1] backtest) so the crossing detection and trajectory grading
are defined ONCE and can never drift. Sign-free; the rising leg IS the direction. Research only.
"""

from __future__ import annotations

import numpy as np

NEAR = 3          # crossings within ±NEAR strikes of spot count as "near-money"


def mins(t: str) -> int:
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


def _frac(a, b):
    return (b - a) / a if (a and a == a and b == b) else 0.0


def load_day(store: str, day: str):
    """A day's aligned 1-min arrays, or None. Keys: stamps, n, spot, CL/PL (call/put close),
    CO/PO (call/put OI), strikes, step. Both premium AND OI are genuine 1-min in the Dhan store."""
    from data.dhan import load_intraday_day
    df = load_intraday_day(store, day)
    if df is None or df.empty:
        return None
    stamps = sorted(df["time_ist"].unique(), key=mins)
    idx = {t: i for i, t in enumerate(stamps)}
    n = len(stamps)
    spot = np.full(n, np.nan)
    for t, v in df.groupby("time_ist")["spot"].first().items():
        spot[idx[t]] = v
    CL, PL, CO, PO = {}, {}, {}, {}

    def _ser(g, col):
        a = np.full(n, np.nan)
        for t, v in g.set_index("time_ist")[col].items():
            if t in idx:
                a[idx[t]] = v
        return a
    for (K, sd), g in df.groupby(["strike", "side"]):
        g = g.drop_duplicates("time_ist")
        if sd == "CE":
            CL[K], CO[K] = _ser(g, "close"), _ser(g, "oi")
        else:
            PL[K], PO[K] = _ser(g, "close"), _ser(g, "oi")
    strikes = sorted(K for K in CL if K in PL)
    if len(strikes) < 5:
        return None
    step = min((b - a) for a, b in zip(strikes, strikes[1:]))
    return {"stamps": stamps, "n": n, "spot": spot, "CL": CL, "PL": PL,
            "CO": CO, "PO": PO, "strikes": strikes, "step": step}


def crossings(d: dict, need_after: int):
    """Yield each first near-money crossing with ≥ ``need_after`` minutes of room after it:
    {K, i (cross index), up (bool), dirs (±1), tod (minute-of-day)}. First each direction only."""
    spot, n, step = d["spot"], d["n"], d["step"]
    CL, PL, stamps = d["CL"], d["PL"], d["stamps"]
    for K in d["strikes"]:
        cl, pl = CL[K], PL[K]
        diff = np.where((cl == cl) & (pl == pl), cl - pl, np.nan)
        seen = set()
        for i in range(1, n):
            a, b = diff[i - 1], diff[i]
            if not (a == a and b == b):
                continue
            up = a < 0 <= b
            dn = a > 0 >= b
            if not (up or dn) or (K, up) in seen:
                continue
            if abs(K - spot[i]) > NEAR * step or i + need_after >= n:
                continue
            seen.add((K, up))
            yield {"K": K, "i": i, "up": up, "dirs": 1.0 if up else -1.0, "tod": mins(stamps[i])}


def winning_leg(d: dict, cr: dict):
    """The ITM-going (rising) leg's 1-min premium array for a crossing — what you'd buy."""
    return d["CL"][cr["K"]] if cr["up"] else d["PL"][cr["K"]]


def trajectory_scores(d: dict, cr: dict, search: int) -> dict:
    """{minute: blast score} over 1..search min after the cross (expanding window from the cross).
    Uses the same sign-free grade_blast the live dashboard uses (ITM-side cover + thrust + wall)."""
    from app.gammablast import grade_blast
    K, i, up = cr["K"], cr["i"], cr["up"]
    step, n = d["step"], d["n"]
    itm_l = d["CL"][K] if up else d["PL"][K]
    itm_o = d["CO"][K] if up else d["PO"][K]
    Kah = K + step if up else K - step
    aoi = d["CO"].get(Kah) if up else d["PO"].get(Kah)
    scores = {}
    for m in range(1, search + 1):
        j = i + m
        if j >= n or itm_o[j] != itm_o[j] or itm_l[j] != itm_l[j]:
            continue
        wr = (aoi[j] / itm_o[j]) if (aoi is not None and itm_o[j] and aoi[j] == aoi[j]) else 0.0
        we = _frac(aoi[i], aoi[j]) if aoi is not None else 0.0
        scores[m] = grade_blast(_frac(itm_o[i], itm_o[j]), _frac(itm_l[i], itm_l[j]), wr, we)["score"]
    return scores


def first_true(scores: dict, search: int, thresh: int = 60):
    """First minute the score reaches ``thresh`` (TRUE), else None."""
    return next((m for m in range(1, search + 1) if scores.get(m, -1) >= thresh), None)
