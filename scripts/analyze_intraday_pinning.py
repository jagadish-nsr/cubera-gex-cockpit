"""Study #2 — intraday pinning: is spot pulled toward the peak-OI strike, more so near expiry?

Ni–Pearson–Poteshman: near expiry, dealer hedging of concentrated OI pins the underlying to the
high-OI ("max-pain"-ish) strike. Sign-free directional test on the 5-min feature cache: at each
stamp, pin_gap = (peak_oi_strike − spot)/spot. If pinning holds, pin_gap should POSITIVELY predict
the forward return (price drifts toward the pin), and the effect should strengthen as DTE→0.

Controls:
  • de-trended: subtract each day's mean forward return (removes index drift on the day).
  • by DTE bucket (expiry-day vs far) — the discriminating prediction.
  • momentum benchmark: IC of trailing 15-min return vs the same forward return (is the pin just
    proxying mean-reversion?).

  python scripts/analyze_intraday_pinning.py            # NIFTY

⚠️ Exploratory; overlapping windows autocorrelate; peak_oi_strike is discrete; sign-free."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HORIZONS_MIN = [15, 30, 60]
GRID = 5


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
        _print("No feature cache — build it first (app.intraday_features.build).")
        return
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)
    f["pin_gap"] = (f["peak_oi_strike"] - f["spot"]) / f["spot"]

    parts = []
    for _, g in f.groupby("day"):
        g = g.copy()
        sp = g["spot"].to_numpy()
        trail = np.full(len(g), np.nan)                 # trailing 15-min (3-step) return
        for i in range(3, len(g)):
            trail[i] = sp[i] / sp[i - 3] - 1.0
        g["trail15"] = trail
        g["open_gap"] = (sp[0] - sp) / sp               # neutral anchor: reversion to day open
        sma = pd.Series(sp).rolling(6, min_periods=6).mean().to_numpy()   # 30-min SMA anchor
        g["sma_gap"] = (sma - sp) / sp
        for h in HORIZONS_MIN:
            m = h // GRID
            fwd = np.full(len(g), np.nan)
            for i in range(len(g) - m):
                fwd[i] = sp[i + m] / sp[i] - 1.0
            g[f"fwd_{h}"] = fwd
            g[f"fwd_{h}_dt"] = g[f"fwd_{h}"] - g[f"fwd_{h}"].mean()   # de-trended (day-neutral)
        parts.append(g)
    f = pd.concat(parts, ignore_index=True)

    dte = f["dte"]
    f["dte_bucket"] = np.where(dte <= 1.5, "≤1.5d (expiry)",
                        np.where(dte <= 3.5, "1.5–3.5d",
                        np.where(dte <= 6.5, "3.5–6.5d", ">6.5d")))
    order = ["≤1.5d (expiry)", "1.5–3.5d", "3.5–6.5d", ">6.5d"]

    _print(f"Intraday pinning: pin_gap=(peakOI−spot)/spot → forward return  ·  {store}  ·  "
           f"{f['day'].nunique()} days\n")
    _print("Spearman IC of pin_gap vs DE-TRENDED forward return, by DTE bucket "
           "(positive = price drifts toward the pin):")
    _print(f"  {'DTE bucket':>16} | " + "  ".join(f"{h}m" for h in HORIZONS_MIN) + "     n")
    for b in order:
        sub = f[f["dte_bucket"] == b]
        cells = []
        for h in HORIZONS_MIN:
            d = sub[["pin_gap", f"fwd_{h}_dt"]].dropna()
            ic = d["pin_gap"].corr(d[f"fwd_{h}_dt"], method="spearman") if len(d) >= 200 else float("nan")
            cells.append(f"{ic:+.3f}")
        _print(f"  {b:>16} | " + "  ".join(cells) + f"   {len(sub):,}")

    _print("\nBenchmarks (pooled, de-trended forward return) — does OI location beat NEUTRAL anchors?")
    for label, col in [("pin_gap → fwd (OI anchor)", "pin_gap"),
                       ("open_gap → fwd (revert to open)", "open_gap"),
                       ("sma_gap → fwd (revert to 30m SMA)", "sma_gap"),
                       ("trailing-15m → fwd (momentum)", "trail15")]:
        cells = []
        for h in HORIZONS_MIN:
            d = f[[col, f"fwd_{h}_dt"]].dropna()
            cells.append(f"{h}m {d[col].corr(d[f'fwd_{h}_dt'], method='spearman'):+.3f}")
        _print(f"  {label:>28}: " + "   ".join(cells))

    _print("\n  Prediction: pin_gap IC should be POSITIVE and LARGEST in the ≤1.5d expiry bucket, "
           "fading with DTE. ⚠️ Overlapping windows autocorrelate; sign-free.")


if __name__ == "__main__":
    main()
