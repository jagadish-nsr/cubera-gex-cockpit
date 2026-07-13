"""Study #4 — signed GEX × bucket: trending vs mean-reverting, and which SIGN is the calm one?

|GEX| (magnitude) dampens vol (confirmed, sign-free). This asks the SIGN question the landmine
leaves open: does the dealer-gamma SIGN split the tape into a trending regime vs a mean-reverting
one — and which sign labels which? Theory (dealer hedging):
  • LONG-gamma dealers (positive GEX in the CORRECT model) fade moves → mean-reversion, calmer.
  • SHORT-gamma dealers (negative GEX) chase moves → trend-continuation, wilder.
So the discriminating fact is the CONTINUATION sign in each signed-GEX bucket:
  continuation IC = Spearman(trailing-15m return, forward return); +ve = trends, −ve = mean-reverts.
Whichever sign convention makes POSITIVE GEX the mean-reverting+calm tail is the right one.

net_gex in the cache uses MODEL A (long call gamma / short put gamma). If model B (India-inverted)
is correct, flip the sign reading: what prints as "most negative" is really the long-gamma tail.

  python scripts/analyze_intraday_gex_sign.py            # NIFTY   [store]

⚠️ Exploratory; overlapping windows autocorrelate; SIGN-DEPENDENT (that's the point)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HORIZONS_MIN = [15, 30, 60]
GRID = 5
BARS_PER_YEAR = 252 * 75


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
        _print("No feature cache — build it first.")
        return
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)

    parts = []
    for _, g in f.groupby("day"):
        g = g.copy()
        sp = g["spot"].to_numpy()
        lr = np.log(sp)
        r5 = np.diff(lr, prepend=np.nan)
        trail = np.full(len(g), np.nan)
        for i in range(3, len(g)):
            trail[i] = sp[i] / sp[i - 3] - 1.0
        g["trail15"] = trail
        for h in HORIZONS_MIN:
            m = h // GRID
            fwd = np.full(len(g), np.nan)
            rv = np.full(len(g), np.nan)
            for i in range(len(g) - m):
                fwd[i] = sp[i + m] / sp[i] - 1.0
                w = r5[i + 1:i + 1 + m]
                if np.isfinite(w).sum() >= 2:
                    rv[i] = np.nanstd(w) * np.sqrt(BARS_PER_YEAR) * 100
            g[f"fwd_{h}"] = fwd
            g[f"rv_{h}"] = rv
        parts.append(g)
    f = pd.concat(parts, ignore_index=True)

    # 5 signed-GEX buckets (quintiles of the SIGNED value, model A).
    f["gq"] = pd.qcut(f["net_gex"], 5, labels=["Q1 most −", "Q2 −", "Q3 ~0", "Q4 +", "Q5 most +"])

    _print(f"Signed GEX (model A) × bucket → regime  ·  {store}  ·  {f['day'].nunique()} days, "
           f"{len(f):,} stamps\n")
    _print(f"  {'bucket':<10} {'median net_gex':>15} | "
           + "  ".join(f"cont{h}" for h in HORIZONS_MIN) + " | "
           + "  ".join(f"rv{h}" for h in HORIZONS_MIN) + " | " + "meanFwd60")
    for b in ["Q1 most −", "Q2 −", "Q3 ~0", "Q4 +", "Q5 most +"]:
        sub = f[f["gq"] == b]
        med = sub["net_gex"].median()
        conts, rvs = [], []
        for h in HORIZONS_MIN:
            d = sub[["trail15", f"fwd_{h}"]].dropna()
            conts.append(d["trail15"].corr(d[f"fwd_{h}"], method="spearman"))
            rvs.append(sub[f"rv_{h}"].mean())
        mf = sub["fwd_60"].dropna().mean() * 100
        _print(f"  {b:<10} {med:>15.2e} | "
               + "  ".join(f"{c:+.3f}" for c in conts) + " | "
               + "  ".join(f"{v:4.1f}" for v in rvs) + f" | {mf:+.3f}%")

    # Magnitude-controlled sign test: within each |GEX| tercile, split by SIGN and compare.
    # This is the only cut that can distinguish model A from B (vol alone can't — |GEX| is
    # sign-invariant). If at MATCHED magnitude the negative-GEX side is more TRENDING/wilder than
    # the positive side, that supports model A (negative = short gamma).
    f["absg"] = f["net_gex"].abs()
    q1, q2 = f["absg"].quantile([1 / 3, 2 / 3])
    f["mag"] = np.where(f["absg"] <= q1, "LOW |GEX|",
               np.where(f["absg"] >= q2, "HIGH |GEX|", "MID |GEX|"))
    f["sgn"] = np.where(f["net_gex"] >= 0, "pos", "neg")
    _print("\nMagnitude-controlled sign test (cont60 / rv60 / n), by |GEX| tercile × sign:")
    _print(f"  {'|GEX| band':<11} | {'NEG-GEX cont60/rv60/n':>28} | {'POS-GEX cont60/rv60/n':>28}")
    for band in ("LOW |GEX|", "MID |GEX|", "HIGH |GEX|"):
        cells = []
        for sg in ("neg", "pos"):
            sub = f[(f["mag"] == band) & (f["sgn"] == sg)]
            d = sub[["trail15", "fwd_60"]].dropna()
            c = d["trail15"].corr(d["fwd_60"], method="spearman") if len(d) >= 200 else float("nan")
            rv = sub["rv_60"].mean()
            cells.append(f"{c:+.3f} / {rv:4.1f} / {len(sub):>6,}")
        _print(f"  {band:<11} | {cells[0]:>28} | {cells[1]:>28}")

    _print("\n  cont_h   = Spearman(trailing-15m, forward-h) — POSITIVE = trends, NEGATIVE = mean-reverts.")
    _print("  rv_h     = mean forward realized vol (annualized %). meanFwd60 = directional bias (≈0 expected).")
    _print("  READ: the correct dealer sign makes the POSITIVE-GEX tail the MEAN-REVERTING + CALM one.")
    _print("        If instead the MOST-NEGATIVE (Q1) tail is the calm/mean-reverting one, that supports")
    _print("        MODEL B (India-inverted) — the true long-gamma tail is printing as negative.")
    _print("  ⚠️ Sign-dependent by design; overlapping windows autocorrelate.")


if __name__ == "__main__":
    main()
