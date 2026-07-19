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


def tpo_histogram(spot, bin_pts: float = 5.0, va_frac: float = 0.70) -> dict | None:
    """1-min TPO time-at-price profile from a spot Series (any subset of a day's 1-min bars, so it
    can be computed point-in-time from bars ≤ t). Returns the histogram ARRAYS (centers, counts +
    the POC/VA bin indices — for rendering the profile) plus the levels + shape stats. None if too
    few bars / degenerate. Time unit = one bar (minutes at each price bin)."""
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
    centers = b0 + (np.arange(nb) + 0.5) * bin_pts
    total = float(counts.sum())
    poc_i = int(counts.argmax())
    poc = float(centers[poc_i])
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
        # histogram arrays (for rendering the profile) + bin geometry
        "centers": centers, "counts": counts, "bin_pts": float(bin_pts),
        "poc_i": poc_i, "val_i": li, "vah_i": ri,
    }


_LEVEL_KEYS = ("poc", "vah", "val", "va_width", "hi", "lo", "day_range",
               "va_width_pct", "tpoc_ratio", "n_modes", "open", "close", "n_bars")


def day_profile(spot, bin_pts: float = 5.0, va_frac: float = 0.70) -> dict | None:
    """Levels + shape stats only (the studies' shape), without the histogram arrays."""
    h = tpo_histogram(spot, bin_pts, va_frac)
    return {k: h[k] for k in _LEVEL_KEYS} if h else None


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


def _journal_spot(symbol: str, day: str, source: str | None):
    """One day's spot Series from the LIVE per-strike journal (source-routed, e.g. Dhan), sorted by
    time — so the overlay uses the same feed the dashboard is on. None if absent/too short."""
    import pandas as pd
    from app.timeseries import load_strikes_day, oistrikes_dir_for
    df = load_strikes_day(day, symbol, directory=oistrikes_dir_for(source))
    if getattr(df, "empty", True) or "spot" not in df.columns:
        return None
    s = pd.to_numeric(df.groupby("time_ist")["spot"].first().sort_index(), errors="coerce").dropna()
    return s if len(s) >= 30 else None


def _spot_for(symbol: str, day: str, source: str | None):
    """A day's 1-min spot: live journal first (current feed), else the Dhan intraday store."""
    return _journal_spot(symbol, day, source) or spot_path(symbol, day)


def _recent_days(symbol: str, source: str | None, before: str) -> list[str]:
    """Sorted trading days with data strictly before ``before`` — the live journal (source-routed)
    first; if it has none yet before ``before`` (cold start / gap), fall back to the Dhan store."""
    from app.timeseries import oistrikes_dir_for
    from data.dhan import available_intraday_days
    jdir = oistrikes_dir_for(source) / symbol
    jdays = [d for d in (sorted(p.stem for p in jdir.glob("*.csv")) if jdir.exists() else []) if d < before]
    if jdays:
        return jdays
    return [d for d in available_intraday_days(symbol) if d < before]


def overlay_profiles(symbol: str, source: str | None, today: str, bin_pts: float | None = None,
                     weekly: bool = False, live_spot: float | None = None) -> dict:
    """The overlay bundle for the dashboard: TODAY's developing profile (with histogram, for the
    bars), the PRIOR trading day's profile, and (if ``weekly``) a 5-day composite. All best-effort;
    missing pieces are None. Point-in-time: prior/weekly use only days strictly before today."""
    import numpy as np
    import pandas as pd
    bp = bin_pts or BIN_PTS.get(symbol, 5.0)
    ts = _journal_spot(symbol, today, source)
    if ts is not None and live_spot and live_spot == live_spot:
        ts = pd.concat([ts, pd.Series([float(live_spot)])])          # include the current live tick
    today_prof = tpo_histogram(ts, bp) if ts is not None else None

    recent = _recent_days(symbol, source, today)
    prior = None
    if recent:
        ps = _spot_for(symbol, recent[-1], source)
        prior = day_profile(ps, bp) if ps is not None else None
    week = None
    if weekly and recent:
        arrs = [s.to_numpy() for d in recent[-5:] if (s := _spot_for(symbol, d, source)) is not None]
        if arrs:
            week = day_profile(pd.Series(np.concatenate(arrs)), bp)
    return {"today": today_prof, "prior": prior, "weekly": week,
            "prior_day": recent[-1] if recent else None, "n_recent": len(recent)}


def profile_regime(prof: dict | None) -> dict | None:
    """A human read of the DEVELOPING distribution's shape (the vol-regime content that survives):
    single-print / rotational vs double-distribution / trending, and how tightly time concentrates
    at the POC (pin strength). Returns {shape, pin, tpoc, n_modes} or None. Descriptive, not a signal."""
    if not prof or prof.get("tpoc_ratio") != prof.get("tpoc_ratio"):
        return None
    t, m = prof["tpoc_ratio"], prof["n_modes"]
    shape = ("double-distribution" if m >= 2 else
             "single-print" if t >= 0.10 else "spread")
    pin = "tight" if t >= 0.14 else "loose" if t >= 0.07 else "thin"
    return {"shape": shape, "pin": pin, "tpoc": t, "n_modes": m}


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
