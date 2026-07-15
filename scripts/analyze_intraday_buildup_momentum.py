"""Study #23 — does INTRADAY momentum (trailing 30/60-min return) sharpen the intraday
positioning-BUILD-UP direction? The multi-year proxy for the dashboard's futures build-up.

The futures build-up (`app/futures_buildup.py`) tags direction from price×ΔOI, but intraday
futures OI caps at ~90 days (Dhan). So we test the same idea at 5-YEAR scale on the options
5-min feature cache, using the sign-free positioning proxy the cache carries:
  BUILD-UP score = classify(sign(Δspot over last 15m), sign(ΣΔtot_OI over last 15m)) mapped to
                   long-build +1 / short-build −1 / short-cover +0.5 / long-unwind −0.5   (price×OI matrix)
  MOM30, MOM60   = trailing 30-/60-min spot return (the momentum factor the user asked for)
  TARGET         = forward 30-/60-min spot return, PER-DAY DEMEANED (strip the day's own drift —
                   the S4-ORB lesson; tests deviations from trend, not the trend)
All factors are point-in-time (use only data ≤ t); the trailing-momentum and forward windows do
not overlap. Honest metrics: univariate pooled IC AND per-DAY-averaged IC (day = the independent
unit; pooled rows autocorrelate), plus a chronological day-block 70/30 OOS for the COMBO vs each
factor alone. Both books.

  python scripts/analyze_intraday_buildup_momentum.py            # NIFTY   [store]

⚠️ EXPLORATORY. Options-positioning proxy, not true futures OI. Overlapping forward windows
autocorrelate (hence per-day IC + day-block OOS). Signed directional bet, sign-free of the GEX
landmine (uses ΔOI direction + price, never the dealer-gamma sign). Not a trade rule."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intraday_features import load_features            # noqa: E402

STEP = 5                         # cache grid minutes
W_BUILD = 3                      # 15-min window (3×5m) for the build-up Δprice/ΔOI
H30, H60 = 6, 12                 # 30-/60-min horizons in grid steps
_SCORE = {("up", "up"): 1.0, ("down", "up"): -1.0, ("up", "down"): 0.5, ("down", "down"): -0.5}


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _pooled_ic(x, y):
    s = pd.DataFrame({"x": x, "y": y}).dropna()
    return (s["x"].corr(s["y"], method="spearman"), len(s)) if len(s) >= 50 else (np.nan, len(s))


def _perday_ic(df, xcol, ycol):
    """Mean of within-day Spearman ICs (day = independent unit), + its std across days."""
    ics = []
    for _, g in df.groupby("day"):
        s = g[[xcol, ycol]].dropna()
        if len(s) >= 12 and s[xcol].nunique() > 2:
            ic = s[xcol].corr(s[ycol], method="spearman")
            if ic == ic:
                ics.append(ic)
    if len(ics) < 30:
        return np.nan, np.nan, len(ics)
    a = np.array(ics)
    return float(a.mean()), float(a.std() / np.sqrt(len(a))), len(a)


def _build(store):
    f = load_features(store)
    if f is None:
        return None
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)
    f["d_tot_oi"] = pd.to_numeric(f.get("d_tot_oi"), errors="coerce")
    gs = f.groupby("day")["spot"]
    # within-day trailing/forward moves via grouped shift/rolling (clean day boundaries, no apply)
    dprice_w = f["spot"] - gs.shift(W_BUILD)
    doi_w = f.groupby("day")["d_tot_oi"].transform(
        lambda s: s.rolling(W_BUILD, min_periods=W_BUILD).sum())
    d = pd.DataFrame({"day": f["day"], "time_ist": f["time_ist"],
                      "dprice_w": dprice_w, "doi_w": doi_w,
                      "mom30": (f["spot"] / gs.shift(H30) - 1.0) * 100,
                      "mom60": (f["spot"] / gs.shift(H60) - 1.0) * 100,
                      "fwd30": (gs.shift(-H30) / f["spot"] - 1.0) * 100,
                      "fwd60": (gs.shift(-H60) / f["spot"] - 1.0) * 100})

    def _score(dp, do):
        if not (dp == dp and do == do) or dp == 0 or do == 0:
            return np.nan
        return _SCORE[("up" if dp > 0 else "down", "up" if do > 0 else "down")]

    d["build"] = [_score(a, b) for a, b in zip(d["dprice_w"], d["doi_w"])]
    # per-day demean the targets (strip each day's own drift)
    for h in ("fwd30", "fwd60"):
        d[h] = d[h] - d.groupby("day")[h].transform("mean")
    return d.dropna(subset=["build"]).reset_index(drop=True)


def main() -> None:
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    d = _build(store)
    if d is None or len(d) < 2000:
        _print(f"Feature cache missing/short for {store}."); return

    _print(f"Intraday build-up × momentum → forward return  ·  {store}  ·  {d['day'].nunique()} days, "
           f"{len(d):,} stamps  ·  build=price×ΔOI(15m), mom=trailing 30/60m\n")

    _print("UNIVARIATE — pooled IC (rows) and per-DAY-averaged IC (day = independent unit):")
    _print(f"  {'factor→target':<22} {'pooled IC':>10} {'per-day IC':>18}  {'days':>5}")
    for xc in ("build", "mom30", "mom60"):
        for yc in ("fwd30", "fwd60"):
            pic, n = _pooled_ic(d[xc], d[yc])
            dic, se, nd = _perday_ic(d, xc, yc)
            _print(f"  {xc+'→'+yc:<22} {pic:>+10.3f} {dic:>+12.3f} ±{se:.3f}  {nd:>5}")

    # COMBO — chronological day-block 70/30; OLS on z-scored factors (train stats), pooled OOS IC
    days = sorted(d["day"].unique())
    cut = days[int(len(days) * 0.70)]
    tr, te = d[d["day"] < cut], d[d["day"] >= cut]

    def combo_oos(cols, target):
        s_tr = tr[cols + [target]].dropna(); s_te = te[cols + [target]].dropna()
        if len(s_tr) < 500 or len(s_te) < 500:
            return None
        X = s_tr[cols].to_numpy(float); y = s_tr[target].to_numpy(float)
        mu, sd = X.mean(0), X.std(0); sd[sd == 0] = 1.0
        beta = np.linalg.lstsq(np.column_stack([np.ones(len(X)), (X - mu) / sd]), y, rcond=None)[0]
        Xte = (s_te[cols].to_numpy(float) - mu) / sd
        pred = beta[0] + Xte @ beta[1:]
        ic = pd.Series(pred).corr(pd.Series(s_te[target].to_numpy()), method="spearman")
        wsign = "".join(f"{c}{'+' if w >= 0 else '−'}" for c, w in zip(cols, beta[1:]))
        return float(ic), len(s_te), wsign

    _print(f"\nCOMBO walk-forward OOS (train days <{cut}, test ≥) — pooled test IC, "
           "does build+momentum beat either alone? (weight signs shown: − = the model FADES it)")
    _print(f"  {'factors → target':<30} {'OOS test IC':>12}  {'weight signs':>16}  {'n_te':>7}")
    for target in ("fwd30", "fwd60"):
        for cols in (["build"], ["mom30"], ["mom60"], ["build", "mom30"], ["build", "mom60"],
                     ["build", "mom30", "mom60"]):
            r = combo_oos(cols, target)
            if r:
                _print(f"  {'+'.join(cols)+' → '+target:<30} {r[0]:>+12.3f}  {r[2]:>16}  {r[1]:>7,}")
        _print("")
    _print("  READ: univariate ICs are all NEGATIVE → the intraday signal is generic MEAN-REVERSION "
           "(recent move fades), not continuation. The combo's positive OOS IC is the OLS fading it "
           "(weight sign −). 'Momentum helps' only if a build+mom combo's IC clearly beats the best "
           "single factor — if mom-alone already matches build+mom, the build-up adds nothing. Per-day "
           "IC (±SE over days) is the honest precision; pooled rows autocorrelate. Sign-free of GEX; "
           "tradeability net of 5-min bid-ask/costs is a SEPARATE, doubtful question (see §5 thrust).")


if __name__ == "__main__":
    main()
