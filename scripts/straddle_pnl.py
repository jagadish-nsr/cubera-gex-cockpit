"""Straddle-at-crossing P&L: is the model's 'big move likely' a tradeable NON-DIRECTIONAL edge?

The blast model can't call DIRECTION, but it flags when a big move is likely EITHER way (it
learned move size / vol). A straddle profits from size regardless of direction — IF the move beats
theta + spread. Test: train the model (walk-forward), then on the TEST set buy the crossed-strike
straddle at entry (io, 10 min after the cross) and exit 30 min later, and see whether the model's
high-blast-probability crossings have positive P&L net of cost — and whether they beat the
unconditional 'buy every straddle' baseline (which theta should make negative).

  python scripts/straddle_pnl.py            # NIFTY  [big_pct]  [cost_pct_roundtrip]

⚠️ Near-expiry straddles bleed theta fast; cost is a rough round-trip % of premium. Exploratory."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FEATURES = ["cover", "thrust", "wall_ratio", "wall_erode", "mom_obs", "iv", "div_obs",
            "skew", "turnover", "pcr", "rv_sofar", "absg", "dist_step", "dir", "dte", "tod"]


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    import numpy as np
    import pandas as pd
    from sklearn.ensemble import HistGradientBoostingClassifier

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    big = float(sys.argv[2]) if len(sys.argv) > 2 else 0.15
    cost = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0     # round-trip % of straddle premium
    df = pd.read_parquet(ROOT / "data" / "journal" / "blast_ml" / f"{store}.parquet")
    df = df.sort_values("day").reset_index(drop=True)
    df = df[(df["strad_entry"] > 0) & df["strad_exit"].notna()].copy()
    df["y"] = (df["cont"] > big).astype(int)
    df["pnl"] = (df["strad_exit"] / df["strad_entry"] - 1.0) * 100 - cost   # net straddle P&L %

    days = sorted(df["day"].unique())
    cut = days[int(len(days) * 0.70)]
    tr, te = df[df["day"] < cut], df[df["day"] >= cut].copy()
    gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300,
                                        l2_regularization=1.0, min_samples_leaf=60,
                                        random_state=0).fit(tr[FEATURES].to_numpy(float), tr["y"].to_numpy())
    te["p"] = gb.predict_proba(te[FEATURES].to_numpy(float))[:, 1]

    _print(f"Straddle-at-crossing P&L · {store} · target big>{big}% · cost {cost:.1f}%/round-trip\n")
    _print(f"  test crossings: {len(te):,}  (entry = crossed-strike CE+PE at +10min, exit +30min)")
    _print(f"  UNCONDITIONAL (buy every straddle): mean net P&L {te['pnl'].mean():+.2f}%  "
           f"win-rate {100*(te['pnl']>0).mean():.0f}%   (theta baseline — expect negative)\n")
    te["dec"] = pd.qcut(te["p"], 10, labels=False, duplicates="drop")
    _print("By predicted blast-probability decile:")
    _print(f"  {'decile':>7} {'n':>5} {'mean move%':>10} {'gross straddle%':>15} "
           f"{'NET P&L%':>9} {'win%':>6}")
    for d in sorted(te["dec"].dropna().unique()):
        g = te[te["dec"] == d]
        gross = (g["strad_exit"] / g["strad_entry"] - 1.0).mean() * 100
        absmove = ((g["mfe"] - g["mae"]).mean())
        _print(f"  {int(d):>7} {len(g):>5} {absmove:>9.2f}% {gross:>+14.2f}% "
               f"{g['pnl'].mean():>+8.2f}% {100*(g['pnl']>0).mean():>5.0f}%")
    top = te[te["dec"] == te["dec"].max()]
    _print(f"\n  TOP decile (model's most-likely-blast): net P&L {top['pnl'].mean():+.2f}%  "
           f"win {100*(top['pnl']>0).mean():.0f}%  vs unconditional {te['pnl'].mean():+.2f}%")
    _print("  Edge exists only if the top-decile NET P&L is clearly POSITIVE (beats theta+cost) and "
           "> the unconditional. A less-negative-but-still-negative top decile = no tradeable edge.")


if __name__ == "__main__":
    main()
