"""Market Profile (TPO time-at-price) from 1-min spot — realized-behaviour levels + shape stats.

Distinct from the OI/GEX levels (positioning: where contracts are OPEN): a TPO profile is where
price ACTUALLY SPENT TIME, so it is a direct *stickiness* measure — and Study #12 concluded pinning
is stickiness, not attraction, so a profile is plausibly a better encoding of that effect than
peak-OI. Built at native 1-min (far finer than traditional 30-min brackets).

What to expect (from the research arc):
- LEVEL-BASED DIRECTION is low-prior — #12/#14/#16 killed it under mirror / mid-zone placebos three
  ways, and a POC is the same geometry. Any direction test MUST beat a same-distance MIRROR placebo.
- The real content is DAY-TYPE SHAPE — value-area width vs range, mode count, time-at-POC ratio —
  a realized-vol-regime descriptor, and vol regime is the family that survives.

Point-in-time: today's FULL profile is known at today's close → clean for predicting TOMORROW. Any
SAME-DAY POC use must be computed strictly from bars up to t (the full-day POC is the classic
profile look-ahead trap — the shape of the Study #5 +0.64→+0.020 collapse). Decision-support only.
"""

from __future__ import annotations

import numpy as np

# Sensible price bins by index (BankNifty/Sensex move in bigger points).
BIN_PTS = {"NIFTY": 5.0, "NIFTY_M": 5.0, "BANKNIFTY": 20.0, "SENSEX": 20.0}


def _count_modes(counts: np.ndarray, smooth: int = 2, min_prom: float = 0.15) -> int:
    """Distribution modes = local maxima in the (smoothed) time-at-price histogram with height
    ≥ ``min_prom`` of the peak. Normal day → 1; double-distribution → 2; etc."""
    if len(counts) < 3:
        return 1
    k = np.ones(2 * smooth + 1) / (2 * smooth + 1)
    sm = np.convolve(counts, k, mode="same")
    peak = float(sm.max())
    if peak <= 0:
        return 1
    modes = 0
    for i in range(1, len(sm) - 1):
        if sm[i] > sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] >= min_prom * peak:
            modes += 1
    return max(1, modes)


def day_profile(spot, bin_pts: float = 5.0, va_frac: float = 0.70) -> dict | None:
    """1-min TPO time-at-price profile from a spot Series (any subset of a day's 1-min bars, so it
    can be computed point-in-time from bars ≤ t). Returns levels + shape stats, or None if too few
    bars / degenerate. Time unit = one bar (minutes at each price bin)."""
    import pandas as pd
    s = pd.to_numeric(pd.Series(spot), errors="coerce").dropna().astype(float)
    if len(s) < 30:
        return None
    lo, hi = float(s.min()), float(s.max())
    if hi <= lo:
        return None
    b0 = np.floor(lo / bin_pts) * bin_pts
    nb = int((hi - b0) // bin_pts) + 1
    idx = np.clip(((s.to_numpy() - b0) // bin_pts).astype(int), 0, nb - 1)
    counts = np.bincount(idx, minlength=nb).astype(float)          # minutes at each price bin (TPO)
    total = float(counts.sum())
    poc_i = int(counts.argmax())
    poc = float(b0 + (poc_i + 0.5) * bin_pts)
    # value area: expand from the POC to the richer adjacent side until ``va_frac`` of the time.
    li = ri = poc_i
    acc = counts[poc_i]
    while acc < va_frac * total and (li > 0 or ri < nb - 1):
        up = counts[ri + 1] if ri < nb - 1 else -1.0
        dn = counts[li - 1] if li > 0 else -1.0
        if up >= dn:
            ri += 1
            acc += max(up, 0.0)
        else:
            li -= 1
            acc += max(dn, 0.0)
    val = float(b0 + li * bin_pts)
    vah = float(b0 + (ri + 1) * bin_pts)
    day_range = hi - lo
    va_width = vah - val
    return {
        "poc": poc, "vah": vah, "val": val, "va_width": va_width,
        "hi": hi, "lo": lo, "day_range": day_range,
        # shape / day-type (the vol-regime content):
        "va_width_pct": (va_width / day_range) if day_range else float("nan"),  # rotational vs directional
        "tpoc_ratio": float(counts[poc_i] / total) if total else float("nan"),  # concentration at the mode
        "n_modes": _count_modes(counts),                                         # 1 normal / 2 double-dist
        "open": float(s.iloc[0]), "close": float(s.iloc[-1]), "n_bars": int(len(s)),
    }


def spot_path(store: str, day: str):
    """The day's 1-min spot Series (index 'HH:MM', sorted), or None. NIFTY is clean; NIFTY_M/
    BankNifty pre-2025 spot is scale-corrupted (see the spot-feed-corruption note) — a >12% intraday
    range is dropped as a bad-tick day."""
    from data.dhan import load_intraday_day
    df = load_intraday_day(store, day)
    if df is None or getattr(df, "empty", True) or "spot" not in df.columns:
        return None
    import pandas as pd
    sp = df.groupby("time_ist")["spot"].first().sort_index()
    sp = pd.to_numeric(sp, errors="coerce").dropna()
    if len(sp) < 30 or sp.iloc[0] <= 0 or (sp.max() - sp.min()) / sp.iloc[0] > 0.12:
        return None
    return sp


def profiles_frame(store: str = "NIFTY", bin_pts: float | None = None, days: list[str] | None = None):
    """Per-day full-session TPO profile for a store as a DataFrame (index = day), for the studies.
    Each row is ``day_profile`` over the whole session (known at that day's close → point-in-time for
    next-day tests). Bad-tick days are dropped."""
    import pandas as pd
    from data.dhan import available_intraday_days
    bp = bin_pts or BIN_PTS.get(store, 5.0)
    days = days if days is not None else available_intraday_days(store)
    rows = []
    for d in days:
        sp = spot_path(store, d)
        if sp is None:
            continue
        pr = day_profile(sp, bin_pts=bp)
        if pr:
            pr["day"] = d
            rows.append(pr)
    return pd.DataFrame(rows).set_index("day") if rows else pd.DataFrame()
