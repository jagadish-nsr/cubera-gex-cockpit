"""Study #21 — IV SKEW (Xing-Zhang-Zhao) level AND change → forward Nifty return.

The two-point implied-vol construction the literature calls the workhorse, distinct from the crude
premium-ratio proxy (analyze_skew.py) and the whole-chain BKM moment (#19, null). We read the real
per-strike Dhan IV smile at the 09:20 cross-section:
    skew_xing = IV(~2% OTM put) − IV(ATM)          # Xing-Zhang-Zhao smirk
    rr        = IV(~2% OTM put) − IV(~2% OTM call)  # risk-reversal (robust, symmetric)
US finding: a steeper put skew = informed downside demand ⇒ LOWER future returns. The note flagged
the intraday CHANGE as "a cleaner momentum signal than the level" — so we test three predictors:
  (a) LEVEL at 09:20,  (b) DAILY change skew(t)−skew(t−1) at 09:20,  (c) intraday MOMENTUM
      skew(09:45)−skew(09:15) → return AFTER (09:45→15:15, point-in-time clean, no overlap).
Forward returns day-detrended (strip drift), IV+DTE vol-partial, tercile spread, walk-forward OOS.
Both books. Sign REPORTED not assumed (India's writer-dominated book may flip the US sign).

  python scripts/analyze_iv_skew_change.py            # NIFTY   [store]

⚠️ EXPLORATORY. ~2% OTM strike (nearest available in the ATM±10 grid); overlapping horizons
autocorrelate; single-index vs cross-sectional papers; sign-free target; not a trade rule."""

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
TERMINAL = "15:15"
MOM0, MOM1 = "09:15", "09:45"            # intraday skew-momentum window (predictor for 09:45→15:15)
OTM_FRAC = 0.02                          # ~2% OTM each side (near-money, liquid, in the ATM±10 grid)


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _partial(x, y, ctrls):
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


def _iv_nearest(iv_by_strike: pd.Series, target: float) -> float | None:
    """IV at the strike nearest ``target`` (positive IV only), or None."""
    v = iv_by_strike[(iv_by_strike > 0) & np.isfinite(iv_by_strike)]
    if v.empty:
        return None
    k = v.index[(np.abs(v.index.to_numpy() - target)).argmin()]
    return float(v.loc[k])


def _skew(cross: pd.DataFrame) -> tuple[float, float, float] | None:
    """(skew_xing, rr, atm_iv) from one OTM-aware cross-section, or None. IV in vol points (%)."""
    if cross.empty:
        return None
    spot = float(cross["spot"].iloc[0])
    if not (spot > 0):
        return None
    ce = cross[cross["side"] == "CE"].groupby("strike")["iv"].last()
    pe = cross[cross["side"] == "PE"].groupby("strike")["iv"].last()
    atm = _iv_nearest(ce, spot)                                   # ATM call IV
    put = _iv_nearest(pe, spot * (1 - OTM_FRAC))                  # OTM put IV (K<S)
    call = _iv_nearest(ce, spot * (1 + OTM_FRAC))                 # OTM call IV (K>S)
    if atm is None or put is None or call is None:
        return None
    return (put - atm, put - call, atm)


def main() -> None:
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    feat = load_features(store)
    if feat is None:
        _print("No feature cache."); return
    t_spot = feat[feat["time_ist"] == TERMINAL].set_index("day")["spot"]
    e_row = feat[feat["time_ist"] == ENTRY].set_index("day")
    m1_spot = feat[feat["time_ist"] == MOM1].set_index("day")["spot"]
    days = available_intraday_days(store)
    day_list = sorted(d for d in days if d in e_row.index and d in t_spot.index)

    rows = []
    for day in day_list:
        df = load_intraday_day(store, day)
        if df is None:
            continue
        s_e = _skew(df[df["time_ist"] == ENTRY])
        if s_e is None:
            continue
        s_m0 = _skew(df[df["time_ist"] == MOM0])
        s_m1 = _skew(df[df["time_ist"] == MOM1])
        er = e_row.loc[day]
        rows.append({
            "day": day, "spot0": float(er["spot"]), "term": float(t_spot.loc[day]),
            "mom1_spot": float(m1_spot.get(day, np.nan)),
            "skew": s_e[0], "rr": s_e[1],
            "skew_m0": s_m0[0] if s_m0 else np.nan, "skew_m1": s_m1[0] if s_m1 else np.nan,
            "iv": float(er["atm_iv"]) * 100 if er["atm_iv"] == er["atm_iv"] else np.nan,
            "dte": float(er["dte"]) if er["dte"] == er["dte"] else np.nan})
    d = pd.DataFrame(rows).reset_index(drop=True)
    if len(d) < 120:
        _print(f"Only {len(d)} usable days."); return

    term = d["term"].to_numpy()
    d["intraday"] = (d["term"] / d["spot0"] - 1.0) * 100.0
    nxt = np.full(len(d), np.nan)
    for i in range(len(d) - 1):
        nxt[i] = (term[i + 1] / term[i] - 1.0) * 100.0
    d["next1"] = nxt
    d["dskew"] = d["skew"].diff()                                  # daily change
    d["mom"] = d["skew_m1"] - d["skew_m0"]                         # intraday change (predictor)
    d["r_after"] = (d["term"] / d["mom1_spot"] - 1.0) * 100.0      # 09:45 → 15:15 (post-predictor)
    for h in ("intraday", "next1", "r_after"):
        d[h] = d[h] - d[h].median()                               # day-detrend

    _print(f"IV skew (Xing-Zhang-Zhao) → forward return  ·  {store}  ·  {len(d)} days "
           f"({d['day'].iloc[0]} … {d['day'].iloc[-1]})  ·  entry {ENTRY}\n")
    _print(f"  skew_xing = IV(2%OTM put)−IV(ATM): median {d['skew'].median():+.2f} vol-pts  "
           f"| rr median {d['rr'].median():+.2f}  (positive = put-skew / downside fear)\n")

    _print("Spearman IC — predictor vs day-detrended forward return, raw & vol-partialled:")
    _print(f"  {'predictor':<22} {'horizon':<9} {'IC':>7} {'part|IV':>8} {'part|IV+DTE':>12}  {'n':>5}")
    tests = [("skew LEVEL", "skew", "intraday"), ("skew LEVEL", "skew", "next1"),
             ("rr LEVEL", "rr", "next1"),
             ("DAILY Δskew", "dskew", "intraday"), ("DAILY Δskew", "dskew", "next1"),
             ("intraday Δskew (mom)", "mom", "r_after")]
    for lab, col, h in tests:
        s = d[[col, h, "iv", "dte"]].dropna()
        ic = s[col].corr(s[h], method="spearman") if len(s) >= 30 else float("nan")
        p1 = _partial(s[col], s[h], [s["iv"]])
        p2 = _partial(s[col], s[h], [s["iv"], s["dte"]])
        _print(f"  {lab:<22} {h:<9} {ic:>+7.3f} {p1:>+8.3f} {p2:>+12.3f}  {len(s):>5}")

    _print("\nTercile spread — mean detrended forward return, LOW vs HIGH skew LEVEL:")
    q1, q2 = d["skew"].quantile([1 / 3, 2 / 3])
    lo, hi = d[d["skew"] <= q1], d[d["skew"] >= q2]
    for h in ("intraday", "next1"):
        a, b = lo[h].dropna(), hi[h].dropna()
        _print(f"  {h:<9} LOW-skew {a.mean():+.3f}%  HIGH-skew {b.mean():+.3f}%  "
               f"HIGH−LOW {b.mean()-a.mean():+.3f}%  (n {len(a)+len(b)})")

    cut = d["day"].iloc[int(len(d) * 0.70)]
    tr, te = d[d["day"] < cut], d[d["day"] >= cut]
    e1, e2 = tr["skew"].quantile([1 / 3, 2 / 3])
    tl = te[te["skew"] <= e1]["next1"].dropna(); th = te[te["skew"] >= e2]["next1"].dropna()
    if len(tl) >= 15 and len(th) >= 15:
        _print(f"\nWalk-forward OOS (train <{cut}): next1 LOW-skew {tl.mean():+.3f}%  "
               f"HIGH-skew {th.mean():+.3f}%  spread {tl.mean()-th.mean():+.3f}%  (n {len(tl)}+{len(th)})")
    _print("\n  Real edge only if a predictor's partial|IV+DTE holds sign across horizons, the tercile "
           "is monotone, AND OOS agrees. Else IV-skew joins RN-skew (#19) & momentum (#20) — direction dead.")
    _print("⚠️ 2% OTM in the ATM±10 grid; overlapping horizons autocorrelate; single-index; sign-free.")


if __name__ == "__main__":
    main()
