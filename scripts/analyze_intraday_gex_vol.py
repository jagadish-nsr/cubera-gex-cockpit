"""Study #1 (real IV): does intraday |GEX| predict forward realized vol?  (SqueezeMetrics, sign-free)

The one signal the whole research arc found where theory predicts — dealer gamma concentration
DAMPENS realized vol — validated at EOD (IC ≈ −0.20 at 10d). Now tested INTRADAY at 5-min on the
real per-strike-IV feature cache: at each 5-min stamp, |GEX(t)| vs the realized vol of spot over
the NEXT 15 / 30 / 60 min (same-day, point-in-time). Expect NEGATIVE IC (more gamma → calmer).

Controls, because both |GEX| and vol have an intraday U-shape and ATM-IV trivially predicts vol:
  • time-of-day-neutralized: subtract each 5-min stamp's cross-day mean from |GEX| and from RV.
  • ATM-IV partial: Spearman of |GEX|→RV after removing rank(atm_iv) from both (does gamma add
    anything beyond just "IV is high")?

  python scripts/analyze_intraday_gex_vol.py            # NIFTY

⚠️ Exploratory. Overlapping forward windows autocorrelate (IC significance overstated); sign-free."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HORIZONS_MIN = [15, 30, 60]
GRID = 5
BARS_PER_YEAR = 252 * 75          # 75 five-min bars per session


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _partial_spearman(x, y, z):
    """Spearman(x, y | z): correlation of rank-residuals after removing rank(z) linearly."""
    import numpy as np
    import pandas as pd
    rx, ry, rz = (pd.Series(v).rank().to_numpy() for v in (x, y, z))
    A = np.c_[np.ones_like(rz), rz]
    ex = rx - A @ np.linalg.lstsq(A, rx, rcond=None)[0]
    ey = ry - A @ np.linalg.lstsq(A, ry, rcond=None)[0]
    return float(pd.Series(ex).corr(pd.Series(ey)))


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    f = load_features(store)
    if f is None:
        _print("No feature cache — build it first (app.intraday_features.build).")
        return
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)
    f["absgex"] = f["net_gex"].abs()

    # Forward realized vol per stamp, computed WITHIN each day (no cross-day windows).
    parts = []
    for _, g in f.groupby("day"):
        g = g.copy()
        lr = np.log(g["spot"].to_numpy())
        r = np.diff(lr, prepend=np.nan)                 # 5-min log returns
        for h in HORIZONS_MIN:
            m = h // GRID
            rv = np.full(len(g), np.nan)
            for i in range(len(g) - m):
                w = r[i + 1:i + 1 + m]
                if np.isfinite(w).sum() >= 2:
                    rv[i] = np.nanstd(w) * np.sqrt(BARS_PER_YEAR) * 100
            g[f"rv_{h}"] = rv
        parts.append(g)
    f = pd.concat(parts, ignore_index=True)

    # Time-of-day neutralization: subtract each stamp's cross-day mean.
    for col in ["absgex", "atm_iv"] + [f"rv_{h}" for h in HORIZONS_MIN]:
        f[col + "_n"] = f[col] - f.groupby("time_ist")[col].transform("mean")

    n_days = f["day"].nunique()
    _print(f"Intraday |GEX| → forward realized vol  ·  {store}  ·  {n_days} days, "
           f"{len(f):,} stamp-rows\n")
    _print(f"{'horizon':>8} | {'IC raw':>8} {'IC tod-neutral':>15} {'atm_iv IC':>10} "
           f"{'|GEX| | atm_iv':>15}   n")
    for h in HORIZONS_MIN:
        d = f[["absgex", f"rv_{h}", "atm_iv", "absgex_n", f"rv_{h}_n"]].dropna()
        ic = d["absgex"].corr(d[f"rv_{h}"], method="spearman")
        icn = d["absgex_n"].corr(d[f"rv_{h}_n"], method="spearman")
        iciv = d["atm_iv"].corr(d[f"rv_{h}"], method="spearman")
        part = _partial_spearman(d["absgex"], d[f"rv_{h}"], d["atm_iv"])
        _print(f"{h:>6}m  | {ic:>+8.3f} {icn:>+15.3f} {iciv:>+10.3f} {part:>+15.3f}   {len(d):,}")

    _print("\n  IC raw          : |GEX(t)| vs next-h-min realized vol, pooled point-in-time.")
    _print("  IC tod-neutral  : same, after removing each 5-min stamp's mean (kills the U-shape).")
    _print("  atm_iv IC       : benchmark — how well ATM IV alone predicts the same RV.")
    _print("  |GEX| | atm_iv  : partial Spearman — does |GEX| predict RV BEYOND ATM IV?")
    _print("\n  Expect NEGATIVE |GEX| ICs (gamma dampens vol). ⚠️ Overlapping windows autocorrelate.")


if __name__ == "__main__":
    main()
