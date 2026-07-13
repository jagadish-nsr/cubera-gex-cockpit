"""T4b — is the vanna vol-risk-premium residual TRADEABLE?  (regime-timed short-vol backtest)

Study #11 found: at matched IV+DTE, higher |vanna| → larger VRP (IV under-prices the calm vanna
predicts). This tests whether that translates to a real, cost-adjusted, OUT-OF-SAMPLE edge from
SELLING the ATM straddle on high-vanna days. Uses the ACTUAL straddle P&L (premium-based → robust
to the spot bad-ticks that corrupted the RV), controls IV + DTE, applies a cost, and splits
walk-forward.

Seller P&L% = (entry_prem − exit_prem)/entry_prem − cost   (sell ATM straddle 09:20, hold to close).

  python scripts/backtest_vanna_shortvol.py            # NIFTY  [store]  [cost%]

⚠️ Overlapping days; entry→close only (not to expiry); rough cost. A real edge must show a positive
partial-vs-IV+DTE AND a positive OOS net P&L that beats unconditional short-vol."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from straddle_gex_timing import _straddle  # noqa: E402  (sibling; K-aware ATM straddle)

ENTRY, EXIT = "09:20", "15:25"


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _partial(x, y, ctrls, df):
    import numpy as np
    import pandas as pd
    cols = {"x": x, "y": y}
    cols.update({f"z{i}": c for i, c in enumerate(ctrls)})
    d = pd.DataFrame(cols).dropna()
    r = d.rank()
    A = np.c_[np.ones(len(d)), *[r[f"z{i}"] for i in range(len(ctrls))]]
    ex = r["x"] - A @ np.linalg.lstsq(A, r["x"], rcond=None)[0]
    ey = r["y"] - A @ np.linalg.lstsq(A, r["y"], rcond=None)[0]
    return float(pd.Series(ex).corr(pd.Series(ey)))


def main() -> None:
    import numpy as np
    import pandas as pd
    from data.dhan import load_intraday_day, available_intraday_days
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    cost = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    feat = load_features(store)
    fe = feat[feat["time_ist"] == ENTRY].set_index("day")
    rows = []
    for day in available_intraday_days(store):
        if day not in fe.index:
            continue
        df = load_intraday_day(store, day)
        if df is None:
            continue
        en = _straddle(df, ENTRY)
        if en is None or en[1] <= 0:
            continue
        k, prem0, _ = en
        ex = _straddle(df, EXIT, K=k)
        if ex is None:
            continue
        e = fe.loc[day]
        rows.append({"day": day, "seller": (prem0 - ex[1]) / prem0 * 100 - cost,
                     "vanna": abs(float(e["vanna"])), "iv": float(e["atm_iv"]) * 100,
                     "dte": float(e["dte"])})
    df = pd.DataFrame(rows)
    if len(df) < 200:
        _print(f"Only {len(df)} days.")
        return
    df["dband"] = pd.cut(df["dte"], [-1, 1.5, 3, 5, 99], labels=["≤1.5d", "1.5-3d", "3-5d", ">5d"])

    _print(f"Vanna-timed SHORT-vol backtest · {store} · {len(df)} days · sell ATM straddle "
           f"{ENTRY}→{EXIT} · cost {cost:.1f}%\n")
    _print(f"  UNCONDITIONAL sell-every-day: mean net {df['seller'].mean():+.2f}%  "
           f"win {100*(df['seller']>0).mean():.0f}%\n")

    # Clean statistical test: does |vanna| predict the seller P&L BEYOND IV + DTE?
    ic = df["vanna"].corr(df["seller"], method="spearman")
    p_ivdte = _partial(df["vanna"], df["seller"], [df["iv"], df["dte"]], df)
    _print(f"  Spearman(|vanna|, seller P&L)      = {ic:+.3f}")
    _print(f"  partial | IV + DTE                 = {p_ivdte:+.3f}   ← edge only if clearly >0\n")

    # DTE-controlled: HIGH vs LOW vanna seller P&L within each DTE band (+ IV shown for the confound)
    _print("By DTE band — seller net P&L, LOW vs HIGH |vanna| tercile (IV shown to spot the confound):")
    _print(f"  {'DTE band':<8} {'LOW-vanna':>10} {'HIGH-vanna':>11} {'HIGH−LOW':>9}  {'IV lo→hi':>12}")
    for b in ["≤1.5d", "1.5-3d", "3-5d", ">5d"]:
        sub = df[df["dband"] == b]
        if len(sub) < 30:
            continue
        q1, q2 = sub["vanna"].quantile([1 / 3, 2 / 3])
        lo, hi = sub[sub["vanna"] <= q1], sub[sub["vanna"] >= q2]
        _print(f"  {b:<8} {lo['seller'].mean():>+9.2f}% {hi['seller'].mean():>+10.2f}% "
               f"{hi['seller'].mean()-lo['seller'].mean():>+8.2f}%  {lo['iv'].mean():>5.1f}→{hi['iv'].mean():<5.1f}")

    # Walk-forward OOS: train IV-tercile cuts + per-(DTE,IV)-cell vanna median on the first 70%,
    # then on the last 30% SELL only when vanna > that cell's train median. Net P&L vs unconditional.
    days = sorted(df["day"])
    cut = days[int(len(days) * 0.70)]
    tr, te = df[df["day"] < cut].copy(), df[df["day"] >= cut].copy()
    tr["ivb"] = pd.qcut(tr["iv"], 3, labels=False, duplicates="drop")
    iv_edges = tr["iv"].quantile([1 / 3, 2 / 3]).tolist()
    te["ivb"] = np.digitize(te["iv"], iv_edges)
    thr = tr.assign(ivb=tr["ivb"]).groupby(["dband", "ivb"], observed=True)["vanna"].median()
    def _sell(r):
        return r["vanna"] >= thr.get((r["dband"], r["ivb"]), np.inf)
    te["sell"] = te.apply(_sell, axis=1)
    sel = te[te["sell"]]
    _print(f"\nWalk-forward OOS (train <{cut}, test ≥): SELL straddle only when |vanna| > its "
           f"(DTE×IV)-cell train-median:")
    _print(f"  selected {len(sel)}/{len(te)} test days · net P&L {sel['seller'].mean():+.2f}% "
           f"(win {100*(sel['seller']>0).mean():.0f}%)  vs unconditional {te['seller'].mean():+.2f}%")
    _print("\n  Tradeable only if partial|IV+DTE > 0, HIGH−LOW is positive across bands, AND the OOS "
           "selected P&L beats unconditional net of cost. Else the #11 residual doesn't survive to P&L.")


if __name__ == "__main__":
    main()
