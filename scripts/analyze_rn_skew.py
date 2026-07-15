"""Study #19 — model-free RISK-NEUTRAL SKEWNESS (Bakshi-Kapadia-Madan 2003) → forward Nifty return.

The whole-chain successor to analyze_skew.py (which is only a crude 3%-OTM premium-RATIO proxy).
BKM recovers the risk-neutral distribution's skewness by integrating OTM option PRICES across the
full strike cross-section — the option-implied asymmetry the market is pricing, not a two-point
guess. Literature (Xing-Zhang-Zhao; Bali-Murray; Guo et al. on SSE50-ETF — the liquid-Asian-index
analogue): a more negative RN skew = heavier priced left tail = downside fear; cross-sectionally
it tends to precede HIGHER returns (skewness preference). For a single index time series the sign
is murkier — we REPORT it, never assume it, and India's writer-dominated book may flip it.

BKM contracts (written in x = ln(K/S); with the engine's r=q=0 the call/put kernels collapse to a
single continuous function of x, and RN SKEWNESS is T-FREE — no dependence on the expiry landmine):
    V = ∫ g2·M dK,  W = ∫ g3·M dK,  X = ∫ g4·M dK      M(K)= call price if K>S else put price
    g2 = 2(1−x)/K² ,  g3 = (6x−3x²)/K² ,  g4 = (12x²−4x³)/K²
    μ  = −(V/2 + W/6 + X/24)
    SKEW = (W − 3μV + 2μ³) / (V − μ²)^{3/2}
Entry = the cross-section at ENTRY (09:20). Forward returns are day-detrended (strip index drift,
the S4-ORB lesson) 15:15-referenced: same-day (09:20→15:15) and next-session (15:15→next 15:15).

  python scripts/analyze_rn_skew.py            # NIFTY   [store]  [entry]

⚠️ EXPLORATORY. The ATM±10 store TRUNCATES the wings at ~±2.3% → the tail integral is clipped and
|skew| is understated (cross-day VARIATION still carries the read). Overlapping horizons
autocorrelate; single-index time series vs cross-sectional papers; sign-free target; not a trade rule."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intraday_features import load_features            # noqa: E402
from data.dhan import load_intraday_day, available_intraday_days  # noqa: E402

ENTRY = "09:20"
TERMINAL = "15:15"                # user-fixed terminal "close" reference
HORIZONS = ("intraday", "next1", "next2")


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _partial(x, y, ctrls):
    """Rank partial correlation of x,y controlling ctrls (Spearman on residualized ranks)."""
    d = pd.DataFrame({"x": x, "y": y})
    for i, c in enumerate(ctrls):
        d[f"z{i}"] = c
    d = d.dropna()
    if len(d) < 40:
        return float("nan")
    r = d.rank()
    A = np.c_[np.ones(len(d)), *[r[f"z{i}"] for i in range(len(ctrls))]]
    ex = r["x"] - A @ np.linalg.lstsq(A, r["x"], rcond=None)[0]
    ey = r["y"] - A @ np.linalg.lstsq(A, r["y"], rcond=None)[0]
    return float(pd.Series(ex).corr(pd.Series(ey), method="spearman"))


def _rn_skew(cross: pd.DataFrame) -> float | None:
    """BKM risk-neutral skewness from one (day, stamp) OTM option cross-section. r=q=0 → T-free."""
    spot = float(cross["spot"].iloc[0])
    if not (spot > 0):
        return None
    # collapse any duplicate (strike,side) rows (BankNifty store can carry >1 per stamp)
    ce = cross[cross["side"] == "CE"].groupby("strike")["close"].last()
    pe = cross[cross["side"] == "PE"].groupby("strike")["close"].last()
    strikes = sorted(set(cross["strike"]))
    K, M = [], []
    for k in strikes:
        px = ce.get(k) if k > spot else pe.get(k)             # OTM side only
        if px is None or not np.isfinite(px) or px <= 0:
            continue
        K.append(float(k)); M.append(float(px))
    if len(K) < 6:                                            # need a real wing on both sides
        return None
    K = np.asarray(K); M = np.asarray(M)
    if not (K.min() < spot < K.max()):                        # must straddle spot
        return None
    x = np.log(K / spot)
    g2 = 2.0 * (1.0 - x) / K**2
    g3 = (6.0 * x - 3.0 * x**2) / K**2
    g4 = (12.0 * x**2 - 4.0 * x**3) / K**2
    _trap = getattr(np, "trapezoid", np.trapz)               # numpy≥2 renamed trapz→trapezoid
    V = float(_trap(g2 * M, K))
    W = float(_trap(g3 * M, K))
    Xq = float(_trap(g4 * M, K))
    mu = -(V / 2.0 + W / 6.0 + Xq / 24.0)
    var = V - mu * mu
    if var <= 0:
        return None
    return (W - 3.0 * mu * V + 2.0 * mu**3) / var**1.5


def _detrend(s: pd.Series) -> pd.Series:
    """Strip the cross-day drift (median) so we test asymmetry, not the index's rising trend."""
    return s - s.median()


def main() -> None:
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    entry = sys.argv[2] if len(sys.argv) > 2 else ENTRY
    feat = load_features(store)
    if feat is None:
        _print("No feature cache — build it first."); return
    # terminal (15:15) and entry spot per day, from the sign-free cache
    t_spot = feat[feat["time_ist"] == TERMINAL].set_index("day")["spot"]
    e_row = feat[feat["time_ist"] == entry].set_index("day")
    days = available_intraday_days(store)
    day_list = sorted(d for d in days if d in e_row.index and d in t_spot.index)

    rows = []
    for day in day_list:
        df = load_intraday_day(store, day)
        if df is None:
            continue
        cross = df[df["time_ist"] == entry]
        if cross.empty:
            continue
        sk = _rn_skew(cross)
        if sk is None or not np.isfinite(sk):
            continue
        er = e_row.loc[day]
        rows.append({"day": day, "rn_skew": sk, "spot0": float(er["spot"]),
                     "term": float(t_spot.loc[day]),
                     "iv": float(er["atm_iv"]) * 100 if er["atm_iv"] == er["atm_iv"] else np.nan,
                     "dte": float(er["dte"]) if er["dte"] == er["dte"] else np.nan})
    d = pd.DataFrame(rows).reset_index(drop=True)
    if len(d) < 120:
        _print(f"Only {len(d)} usable days — need the intraday store + feature cache."); return

    term = d["term"].to_numpy()
    d["intraday"] = (d["term"] / d["spot0"] - 1.0) * 100.0          # 09:20 → 15:15 (tradeable)
    nxt = np.full(len(d), np.nan); nx2 = np.full(len(d), np.nan)
    for i in range(len(d) - 1):
        nxt[i] = (term[i + 1] / term[i] - 1.0) * 100.0             # 15:15 → next 15:15
    for i in range(len(d) - 2):
        nx2[i] = (term[i + 2] / term[i] - 1.0) * 100.0
    d["next1"], d["next2"] = nxt, nx2
    for h in HORIZONS:
        d[h] = _detrend(d[h])                                       # day-detrended forward return

    _print(f"BKM risk-neutral SKEWNESS → forward return  ·  {store}  ·  {len(d)} days "
           f"({d['day'].iloc[0]} … {d['day'].iloc[-1]})  ·  entry {entry}, terminal {TERMINAL}\n")
    _print(f"  RN skew: median {d['rn_skew'].median():+.3f}  IQR [{d['rn_skew'].quantile(.25):+.3f}, "
           f"{d['rn_skew'].quantile(.75):+.3f}]   (negative = priced left tail / downside fear)\n")

    _print("Spearman IC — RN skew at entry vs day-detrended forward return, raw & vol-partialled:")
    _print(f"  {'horizon':<9} {'IC':>7} {'partial|IV':>11} {'partial|IV+DTE':>15}   {'n':>5}")
    for h in HORIZONS:
        s = d[["rn_skew", h, "iv", "dte"]].dropna()
        ic = s["rn_skew"].corr(s[h], method="spearman") if len(s) >= 30 else float("nan")
        p1 = _partial(s["rn_skew"], s[h], [s["iv"]])
        p2 = _partial(s["rn_skew"], s[h], [s["iv"], s["dte"]])
        _print(f"  {h:<9} {ic:>+7.3f} {p1:>+11.3f} {p2:>+15.3f}   {len(s):>5}")

    _print("\nTercile spread — mean detrended forward return, LOW vs HIGH RN-skew tercile:")
    _print(f"  {'horizon':<9} {'LOW-skew':>9} {'HIGH-skew':>10} {'HIGH−LOW':>9}   {'n':>5}")
    q1, q2 = d["rn_skew"].quantile([1 / 3, 2 / 3])
    lo, hi = d[d["rn_skew"] <= q1], d[d["rn_skew"] >= q2]
    for h in HORIZONS:
        a, b = lo[h].dropna(), hi[h].dropna()
        if len(a) < 20 or len(b) < 20:
            continue
        _print(f"  {h:<9} {a.mean():>+8.3f}% {b.mean():>+9.3f}% {b.mean()-a.mean():>+8.3f}%   "
               f"{len(a)+len(b):>5}")

    # walk-forward OOS: train sets the tercile edges; test trades LONG-low / SHORT-high skew
    cut = d["day"].iloc[int(len(d) * 0.70)]
    tr, te = d[d["day"] < cut], d[d["day"] >= cut]
    e1, e2 = tr["rn_skew"].quantile([1 / 3, 2 / 3])
    _print(f"\nWalk-forward OOS (train <{cut}, edges frozen): LONG low-skew − SHORT high-skew tercile:")
    for h in ("next1",):
        tl = te[te["rn_skew"] <= e1][h].dropna()
        th = te[te["rn_skew"] >= e2][h].dropna()
        if len(tl) >= 15 and len(th) >= 15:
            spread = tl.mean() - th.mean()
            _print(f"  {h}: long-low {tl.mean():+.3f}%  short-high {th.mean():+.3f}%  "
                   f"spread {spread:+.3f}%  (n {len(tl)}+{len(th)})")
    _print("\n  Real edge only if partial|IV+DTE holds sign across horizons, the tercile spread is "
           "monotone, AND OOS spread agrees. Else RN skew joins the 'direction is dead' pile.")
    _print("⚠️ Truncated ±2.3% wings understate |skew|; overlapping horizons autocorrelate; "
           "single-index vs cross-sectional papers; sign-free; NOT a trade rule.")


if __name__ == "__main__":
    main()
