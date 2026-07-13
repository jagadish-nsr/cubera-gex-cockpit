"""ΔPCR conditioned on GEX regime — does the intraday shift in put/call OI predict returns?

Static PCR was null (EOD). This tests the CHANGE in PCR (ΔPCR = the intraday positioning flow —
meaningful only because Dhan OI updates live, unlike NSE's EOD-lagged OI), conditioned on the GEX
regime. Hypothesis: rising PCR (puts building) may mean-revert in the high-gamma (pinning) regime
but continue in the low-gamma (trending) regime — a regime-conditional edge the pooled test washes.

Per 5-min stamp: pcr=putOI/callOI, ΔPCR over the last 30min; |GEX| tercile (regime) + GEX sign;
forward return (de-trended, within-day). IC of ΔPCR vs forward return, pooled and by GEX bin.

  python scripts/analyze_deltapcr_gex.py            # NIFTY  [store]

⚠️ Sign-dependent (India is writer-dominated — rising PCR can be bearish OR put-writing support);
overlapping windows autocorrelate. Exploratory."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HORIZONS = [15, 30, 60]
GRID = 5
LOOKBACK = 6                      # ΔPCR over the last 6×5min = 30 min


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
    f = load_features(store)
    if f is None:
        _print("No feature cache.")
        return
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)
    f["pcr"] = f["tot_put_oi"] / f["tot_call_oi"]

    parts = []
    for _, g in f.groupby("day"):
        g = g.copy()
        g["dpcr"] = g["pcr"] - g["pcr"].shift(LOOKBACK)          # intraday PCR flow
        sp = g["spot"].to_numpy()
        for h in HORIZONS:
            m = h // GRID
            fwd = np.full(len(g), np.nan)
            for i in range(len(g) - m):
                fwd[i] = sp[i + m] / sp[i] - 1.0
            g[f"fwd_{h}"] = fwd
            g[f"fwd_{h}_dt"] = g[f"fwd_{h}"] - np.nanmean(g[f"fwd_{h}"])   # de-trend (day-neutral)
        parts.append(g)
    f = pd.concat(parts, ignore_index=True)

    absg = f["net_gex"].abs()
    q1, q2 = absg.quantile([1 / 3, 2 / 3])
    f["reg"] = np.where(absg <= q1, "LOW |GEX|", np.where(absg >= q2, "HIGH |GEX|", "MID |GEX|"))
    f["gsign"] = np.where(f["net_gex"] >= 0, "GEX+", "GEX−")

    def ic(sub, col):
        d = sub[["dpcr", col]].dropna()
        return d["dpcr"].corr(d[col], method="spearman") if len(d) >= 300 else float("nan")

    n = f["day"].nunique()
    _print(f"ΔPCR (30-min) → forward return, by GEX regime · {store} · {n} days, {len(f):,} stamps\n")
    _print("Spearman IC of ΔPCR vs DE-TRENDED forward return (positive = rising PCR precedes up-move):")
    _print(f"  {'bin':<14} " + "  ".join(f"{h}m" for h in HORIZONS) + "      n")
    for b in ("POOLED", "LOW |GEX|", "MID |GEX|", "HIGH |GEX|", "GEX+", "GEX−"):
        sub = f if b == "POOLED" else (f[f["reg"] == b] if "GEX|" in b else f[f["gsign"] == b])
        cells = "  ".join(f"{ic(sub, f'fwd_{h}_dt'):+.3f}" for h in HORIZONS)
        _print(f"  {b:<14} {cells}   {len(sub):>7,}")

    _print("\n  A regime-conditional lead = a bin with a stable IC clearly beyond ±0.05 (esp. if LOW-|GEX|")
    _print("  and HIGH-|GEX| have OPPOSITE signs → continuation vs reversion). ⚠️ Sign-dependent; overlap.")


if __name__ == "__main__":
    main()
