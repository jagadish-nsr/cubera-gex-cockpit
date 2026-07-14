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


def _max_pain(strikes, coi, poi):
    """Strike that minimizes total option-holder ITM payout at expiry (sign-free). O(n^2), n~21."""
    best_k, best_pain = None, None
    for kexp in strikes:
        pain = 0.0
        for k in strikes:
            if k < kexp:
                pain += coi.get(k, 0.0) * (kexp - k)      # calls ITM if settle above them
            elif k > kexp:
                pain += poi.get(k, 0.0) * (k - kexp)      # puts ITM if settle below them
        if best_pain is None or pain < best_pain:
            best_pain, best_k = pain, kexp
    return best_k


def _gamma_flip(strikes, gex_k):
    """Strike where cumulative net-GEX (low→high) crosses zero, linearly interpolated. Location is
    INVARIANT to a global dealer-sign flip (the landmine) — a ±1 on every term moves the crossing
    nowhere. None if the profile never changes sign."""
    ks = sorted(strikes)
    cum = 0.0
    prev_cum = prev_k = None
    for k in ks:
        cum += gex_k.get(k, 0.0)
        if prev_cum is not None and (prev_cum < 0 <= cum or prev_cum > 0 >= cum):
            if cum != prev_cum:
                return prev_k + (-prev_cum / (cum - prev_cum)) * (k - prev_k)
            return k
        prev_cum, prev_k = cum, k
    return None


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
    from scipy.stats import norm
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
        gex = vanna_ex = charm_ex = tot_c = tot_p = 0.0
        peak_k, peak_oi = spot, -1.0
        atm_k = min(strikes, key=lambda kk: abs(kk - spot))
        atm_iv = None
        sq = T ** 0.5
        coi_by_k, poi_by_k, gex_by_k = {}, {}, {}     # per-strike accumulators for the LEVELS
        for k in strikes:
            coi = float(ce_oi.get(k, 0.0))
            poi = float(pe_oi.get(k, 0.0))
            coi_by_k[k], poi_by_k[k] = coi, poi
            tot_c += coi
            tot_p += poi
            if coi + poi > peak_oi:
                peak_oi, peak_k = coi + poi, k
            iv = float(ce_iv.get(k, np.nan)) if k >= spot else float(pe_iv.get(k, np.nan))  # OTM side
            if k == atm_k and iv == iv and iv > 0:
                atm_iv = iv
            if iv == iv and iv > 0:                                    # all offsets are near-money
                gk = bs_gamma(spot, k, T, iv) * (coi - poi)            # per-strike net dealer GEX
                gex_by_k[k] = gk
                gex += gk
                d1 = (np.log(spot / k) + 0.5 * iv * iv * T) / (iv * sq)   # vanna: sharper vol-regime
                d2 = d1 - iv * sq                                        # metric than GEX (Study #8);
                phi = norm.pdf(d1)                                       # charm: best @0-DTE (T1b blend)
                vanna_ex += (-phi * d2 / iv) * (coi - poi)
                charm_ex += (phi * d2 / (2.0 * T)) * (coi - poi)
        gex *= spot * spot * 0.01
        tot = tot_c + tot_p
        d_oi = (tot - prev_tot) if prev_tot is not None else 0.0
        prev_tot = tot

        # --- per-stamp GEX LEVELS (sign-free geometry for the situational studies) ------------
        calls_above = [(k, coi_by_k[k]) for k in strikes if k >= spot]
        puts_below = [(k, poi_by_k[k]) for k in strikes if k <= spot]
        call_wall = max(calls_above, key=lambda x: x[1])[0] if calls_above else None
        put_wall = max(puts_below, key=lambda x: x[1])[0] if puts_below else None
        wsum = sum(abs(v) for v in gex_by_k.values())                  # gamma-weighted magnet
        magnet = (sum(k * abs(v) for k, v in gex_by_k.items()) / wsum) if wsum > 0 else None
        max_pain = _max_pain(strikes, coi_by_k, poi_by_k)
        gamma_flip = _gamma_flip(strikes, gex_by_k)                     # sign-flip-invariant location

        rows.append({"day": day, "time_ist": st, "spot": spot, "net_gex": gex, "vanna": vanna_ex,
                     "charm": charm_ex, "atm_iv": atm_iv, "dte": T * 365.0, "peak_oi_strike": peak_k,
                     "tot_call_oi": tot_c, "tot_put_oi": tot_p, "d_tot_oi": d_oi,
                     "call_wall": call_wall, "put_wall": put_wall, "magnet": magnet,
                     "max_pain": max_pain, "gamma_flip": gamma_flip})
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


import functools

REGIME_HORIZON_MIN = 30          # forward realized-vol horizon for the live regime banner
_MAX_5M_RET = 0.08               # cap on a 5-min spot log-return — beyond this is a bad Dhan tick,
                                 # not a real index move (guards RV means from spurious spikes)


REGIME_METRIC = "vanna"          # vol-regime metric: |vanna| beats |GEX| beyond IV (Study #8,
                                 # cross-book). Falls back to net_gex on pre-vanna caches.


@functools.lru_cache(maxsize=32)
def _regime_stats(store: str, horizon_min: int, mtime: float, metric: str = REGIME_METRIC,
                  dte_max: float | None = None):
    """Cached (sorted |metric| array, tercile cuts, median forward RV per tercile) from the feature
    cache. ``mtime`` is the cache-file mtime so a rebuilt cache invalidates this automatically.
    ``dte_max`` restricts the distribution to days with DTE ≤ dte_max — needed for the 0-DTE charm
    metric (charm ∝ 1/T explodes near expiry, so it must be ranked within the 0-DTE distribution).
    Sign-free. Forward RV = std of the next horizon_min/5 five-min spot log-returns, annualized."""
    import numpy as np
    f = load_features(store)
    if f is None or len(f) < 1000:
        return None
    col = metric if metric in f.columns else "net_gex"   # graceful fallback for old caches
    if dte_max is not None and "dte" in f.columns:
        f = f[f["dte"] <= dte_max]
    if len(f) < 500:
        return None
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)
    m = max(1, horizon_min // GRID_MIN)
    rv = np.full(len(f), np.nan)
    absg = f[col].abs().to_numpy()
    spot_all = f["spot"].to_numpy()
    for _, idx in f.groupby("day").groups.items():
        ii = np.array(sorted(idx))
        sp = spot_all[ii]
        lr = np.diff(np.log(sp), prepend=np.nan)
        lr = np.clip(lr, -_MAX_5M_RET, _MAX_5M_RET)   # kill bad-tick spikes (index can't move >8%/5m)
        for j in range(len(ii) - m):
            w = lr[j + 1:j + 1 + m]
            if np.isfinite(w).sum() >= 2:
                rv[ii[j]] = np.nanstd(w) * np.sqrt(252 * 75) * 100
    ok = np.isfinite(rv)
    a, r = absg[ok], rv[ok]
    q1, q2 = np.percentile(a, [33, 66])
    # MEDIAN, not mean: ~1.8% of stamps carry corrupt Dhan spot ticks that survive the ±8% clip
    # and destroy a mean forward-RV; the median is robust and gives realistic vol per tercile.
    lo_rv = float(np.median(r[a <= q1]))
    hi_rv = float(np.median(r[a >= q2]))
    mid_rv = float(np.median(r[(a > q1) & (a < q2)]))
    return np.sort(absg), float(q1), float(q2), lo_rv, mid_rv, hi_rv, int(len(a))


DTE_CHARM_MAX = 1.5              # ≤ this DTE the gauge uses |charm| (sharper near expiry, T1b);
                                 # above it uses |vanna|. Validated cross-book (Study #8/T1b).


def regime_now(store: str, vanna_now: float | None, charm_now: float | None,
               dte_now: float | None, horizon_min: int = REGIME_HORIZON_MIN) -> dict | None:
    """The live gauge entry point: DTE-switch between |charm| (≤1.5 DTE, sharper near expiry) and
    |vanna| (else), each ranked within its own DTE-appropriate distribution. Returns the regime
    dict (adds 'metric')."""
    if dte_now is not None and dte_now <= DTE_CHARM_MAX and charm_now is not None:
        return intraday_vol_regime(store, charm_now, horizon_min, metric="charm",
                                   dte_max=DTE_CHARM_MAX)
    return intraday_vol_regime(store, vanna_now, horizon_min, metric="vanna")


def intraday_vol_regime(store: str, value_now: float | None,
                        horizon_min: int = REGIME_HORIZON_MIN,
                        metric: str = REGIME_METRIC, dte_max: float | None = None) -> dict | None:
    """Sign-free live regime read: place the CURRENT |metric| (dealer-vanna by default, or dealer-
    charm at 0-DTE via ``regime_now``; in cache units, see the dashboard scale bridge) in the 5-yr
    intraday distribution (restricted to DTE ≤ ``dte_max`` when given), and return the historical
    median forward realized vol (annualized %, next ``horizon_min``) for its tercile. HIGH →
    calmer/SIDEWAYS; LOW → wilder/TRENDING. None if no cache / no current value."""
    import numpy as np
    p = _cache_path(store)
    if not p.exists() or value_now is None or not np.isfinite(value_now):
        return None
    value_now = abs(value_now)
    stats = _regime_stats(store, horizon_min, p.stat().st_mtime, metric, dte_max)
    if stats is None:
        return None
    srt, q1, q2, lo_rv, mid_rv, hi_rv, n = stats
    pct = 100.0 * float(np.searchsorted(srt, value_now, side="right")) / len(srt)
    if value_now >= q2:
        tag, read, exp_rv = "calm", "HIGH dealer-gamma concentration → SIDEWAYS / mean-reverting", hi_rv
    elif value_now <= q1:
        tag, read, exp_rv = "wild", "LOW dealer-gamma concentration → TRENDING / wider swings", lo_rv
    else:
        tag, read, exp_rv = "mid", "MID dealer-gamma concentration → neutral / mixed", mid_rv
    return {"tag": tag, "read": read, "exp_rv": exp_rv, "lo_rv": lo_rv, "mid_rv": mid_rv,
            "hi_rv": hi_rv, "pct": pct, "n": n, "absgex": float(value_now),
            "horizon_min": horizon_min, "metric": metric}
