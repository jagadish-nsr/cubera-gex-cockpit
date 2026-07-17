"""T4 — vol-risk-premium: does IV over/under-price the vanna-predicted realized vol, by regime?

The intraday straddle (#6b) and the daily GEX straddle (#7) were priced-in — but #7 used GEX and
found "IV already prices the calmer vol". This re-asks with the SHARPER metric (|vanna|) and frames
it as the variance-risk-premium directly: at the open take ATM IV, hold to close, and measure
  VRP = IV − realized_vol   (annualized %; VRP>0 ⇒ IV over-priced vol ⇒ SHORT-vol profits).
If HIGH-vanna (calm-predicted) days carry a SYSTEMATICALLY larger VRP than LOW-vanna days — AFTER
controlling DTE (the #7 confound: vanna∝1/σ, and near-expiry distorts everything) — then IV does
NOT fully price the vanna signal and a regime-timed short-vol trade has edge. Else it's efficient.

  python scripts/analyze_vol_risk_premium.py            # NIFTY  [store]  [entry]

⚠️ Overlapping/autocorrelated days; intraday RV annualized; a real edge must survive the DTE
control AND beat a straddle-cost proxy. Exploratory."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_MAX_5M = 0.08


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    entry = sys.argv[2] if len(sys.argv) > 2 else "09:20"
    f = load_features(store)
    if f is None:
        _print("No feature cache.")
        return
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)

    rows = []
    for day, g in f.groupby("day"):
        g = g.sort_values("time_ist")
        e = g[g["time_ist"] == entry]
        if e.empty or len(g) < 20:
            continue
        e = e.iloc[0]
        iv0 = float(e["atm_iv"]) * 100 if e["atm_iv"] == e["atm_iv"] else np.nan
        if not (iv0 > 0):
            continue
        # realized vol from entry to close (annualized %), robust to bad ticks
        post = g[g["time_ist"] >= entry]["spot"].to_numpy()
        lr = np.clip(np.diff(np.log(post)), -_MAX_5M, _MAX_5M)
        if np.isfinite(lr).sum() < 5:
            continue
        rv = float(np.nanstd(lr)) * np.sqrt(252 * 75) * 100
        rows.append({"day": day, "iv": iv0, "rv": rv, "vrp": iv0 - rv,
                     "vanna": abs(float(e["vanna"])), "dte": float(e["dte"])})
    df = pd.DataFrame(rows)
    if len(df) < 100:
        _print(f"Only {len(df)} days.")
        return

    _print(f"Vol-risk-premium (IV − realized, entry {entry} → close) · {store} · {len(df)} days\n")
    _print(f"  UNCONDITIONAL: mean IV {df['iv'].mean():.1f}%  realized {df['rv'].mean():.1f}%  "
           f"VRP {df['vrp'].mean():+.1f}%  ({100*(df['vrp']>0).mean():.0f}% of days IV>RV)\n")

    # DTE-CONTROLLED: within a DTE band, does higher |vanna| carry a larger VRP (short-vol edge)?
    df["dband"] = pd.cut(df["dte"], [-1, 1.5, 3, 5, 99], labels=["≤1.5d", "1.5-3d", "3-5d", ">5d"])
    _print("DTE-controlled — VRP by |vanna| tercile within each DTE band "
           "(edge = HIGH-vanna VRP > LOW-vanna VRP):")
    _print(f"  {'DTE band':<8} | {'LOW-vanna VRP':>14} {'MID':>8} {'HIGH-vanna VRP':>15} | "
           f"{'IV lo→hi':>12} {'RV lo→hi':>12}")
    for b in ["≤1.5d", "1.5-3d", "3-5d", ">5d"]:
        sub = df[df["dband"] == b]
        if len(sub) < 30:
            continue
        q1, q2 = sub["vanna"].quantile([1 / 3, 2 / 3])
        lo = sub[sub["vanna"] <= q1]; mid = sub[(sub["vanna"] > q1) & (sub["vanna"] < q2)]
        hi = sub[sub["vanna"] >= q2]
        _print(f"  {b:<8} | {lo['vrp'].mean():>+13.1f}% {mid['vrp'].mean():>+7.1f}% "
               f"{hi['vrp'].mean():>+14.1f}% | {lo['iv'].mean():>5.1f}→{hi['iv'].mean():<5.1f} "
               f"{lo['rv'].mean():>5.1f}→{hi['rv'].mean():<5.1f}")

    # Partials: the −0.50 vs DTE is really vanna∝1/IV re-encoding "IV is high". The decisive test
    # is whether vanna predicts VRP BEYOND the IV LEVEL — if not, there's no vanna-specific edge.
    def partial(x, y, ctrls):
        cols = {"x": x, "y": y}
        cols.update({f"z{i}": c for i, c in enumerate(ctrls)})
        d = pd.DataFrame(cols).dropna()
        r = d.rank()
        A = np.c_[np.ones(len(d)), *[r[f"z{i}"] for i in range(len(ctrls))]]
        ex = r["x"] - A @ np.linalg.lstsq(A, r["x"], rcond=None)[0]
        ey = r["y"] - A @ np.linalg.lstsq(A, r["y"], rcond=None)[0]
        return float(pd.Series(ex).corr(pd.Series(ey)))
    v, vrp, iv, dte = df["vanna"], df["vrp"], df["iv"], df["dte"]
    _print(f"\n  Spearman(|vanna|, VRP)        = {v.corr(vrp, method='spearman'):+.3f}")
    _print(f"  partial | DTE                 = {partial(v, vrp, [dte]):+.3f}")
    _print(f"  partial | IV-level            = {partial(v, vrp, [iv]):+.3f}   ← the decisive control")
    _print(f"  partial | IV + DTE            = {partial(v, vrp, [iv, dte]):+.3f}")
    _print(f"  (benchmark) Spearman(IV, VRP) = {iv.corr(vrp, method='spearman'):+.3f}")
    _print("\n  If 'partial | IV' collapses toward 0, the VRP pattern is just 'sell high IV' (a known,"
           " risk-compensated premium) — vanna adds NO tradeable edge beyond IV level (efficient).")
    _print("  ⚠️ Overlapping days; intraday RV; no cost applied here. Exploratory.")


if __name__ == "__main__":
    main()
