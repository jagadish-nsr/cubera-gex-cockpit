"""Study #3 — does a turnover-confirmed premium thrust in an option CONTINUE?  (real n)

The recurring live-only idea ("build-up with turnover is key"; "premium up 3–5% in 5 min + big
turnover") only ever had a handful of captured days. Here it runs on the full Dhan raw store
(1-min per-strike CE/PE close + oi + volume), on the 5-min grid, for the ATM option (off=0),
pooling CE and PE (a premium-thrust test, direction-agnostic on the underlying).

Signals at stamp t (each vs a same-book baseline of all stamps):
  A  3-green      : 3 consecutive positive 5-min premium returns
  B  3-green+TO   : A and 5-min turnover (vol/oi) in the top tercile that day
  C  jump+TO      : single 5-min premium jump ≥ +3% AND top-tercile turnover
Outcome: forward OPTION premium return over next 15 / 30 min (naked long), same session.
We report mean forward return + hit-rate vs baseline — if the thrust "sustains", signal > base.

  python scripts/analyze_intraday_streaks.py            # NIFTY   [store]

⚠️ Exploratory; overlapping/selection effects; theta drags naked-long returns; sign-free."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GRID_MIN = 5
_OPEN = 9 * 60 + 15
JUMP = 0.03                       # +3% single-bar premium jump


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _grid(df):
    def mins(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    return [t for t in sorted(df["time_ist"].unique(), key=mins) if (mins(t) - _OPEN) % GRID_MIN == 0]


def main() -> None:
    import numpy as np
    import pandas as pd
    from data.dhan import load_intraday_day, available_intraday_days

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    days = available_intraday_days(store)
    if not days:
        _print(f"No raw intraday store for {store}.")
        return

    # collectors: forward returns tagged by which signal fired
    base15, base30 = [], []
    sig = {k: {"f15": [], "f30": []} for k in ("A", "B", "C")}

    for di, day in enumerate(days, 1):
        df = load_intraday_day(store, day)
        if df is None:
            continue
        df = df[df["off"] == 0]
        if df.empty:
            continue
        stamps = _grid(df)
        for side in ("CE", "PE"):
            s = df[df["side"] == side].set_index("time_ist")
            close = np.array([float(s.loc[t, "close"]) if t in s.index else np.nan for t in stamps])
            vol = np.array([float(s.loc[t, "volume"]) if t in s.index else np.nan for t in stamps])
            oi = np.array([float(s.loc[t, "oi"]) if t in s.index else np.nan for t in stamps])
            if np.isfinite(close).sum() < 20:
                continue
            ret = np.concatenate([[np.nan], close[1:] / close[:-1] - 1.0])
            to = vol / np.where(oi > 0, oi, np.nan)
            to_hi = np.nanpercentile(to, 66) if np.isfinite(to).any() else np.inf
            n = len(stamps)
            for i in range(n):
                f15 = close[i + 3] / close[i] - 1.0 if i + 3 < n and close[i] > 0 else np.nan
                f30 = close[i + 6] / close[i] - 1.0 if i + 6 < n and close[i] > 0 else np.nan
                if not np.isfinite(f15):
                    continue
                base15.append(f15)
                if np.isfinite(f30):
                    base30.append(f30)
                green3 = i >= 3 and np.all(ret[i - 2:i + 1] > 0)
                to_now = to[i] >= to_hi if np.isfinite(to[i]) else False
                jump = np.isfinite(ret[i]) and ret[i] >= JUMP
                fired = []
                if green3:
                    fired.append("A")
                if green3 and to_now:
                    fired.append("B")
                if jump and to_now:
                    fired.append("C")
                f30ok = np.isfinite(f30)
                for key in fired:
                    sig[key]["f15"].append(f15)
                    if f30ok:
                        sig[key]["f30"].append(f30)
        if di % 200 == 0:
            _print(f"  …{di}/{len(days)} days")

    def stat(a):
        a = np.array([x for x in a if np.isfinite(x)])
        return (len(a), np.mean(a) * 100, (a > 0).mean() * 100) if len(a) else (0, float("nan"), float("nan"))

    _print(f"\nTurnover-confirmed premium thrust → forward ATM-option return  ·  {store}  ·  "
           f"{len(days)} days (CE+PE pooled)\n")
    nb15, mb15, hb15 = stat(base15)
    nb30, mb30, hb30 = stat(base30)
    _print(f"  {'signal':<26} {'n':>8} {'mean f15':>10} {'hit15':>7}   {'mean f30':>10} {'hit30':>7}")
    _print(f"  {'baseline (all stamps)':<26} {nb15:>8,} {mb15:>+9.2f}% {hb15:>6.1f}%   "
           f"{mb30:>+9.2f}% {hb30:>6.1f}%")
    labels = {"A": "3-green streak", "B": "3-green + high TO", "C": "≥3% jump + high TO"}
    for k in ("A", "B", "C"):
        n15, m15, h15 = stat(sig[k]["f15"])
        n30, m30, h30 = stat(sig[k]["f30"])
        _print(f"  {labels[k]:<26} {n15:>8,} {m15:>+9.2f}% {h15:>6.1f}%   {m30:>+9.2f}% {h30:>6.1f}%")

    _print("\n  'Sustains' ⇒ signal mean/hit > baseline. Naked-long theta makes the baseline "
           "mean negative; a real thrust-continuation shows up as LESS negative / positive & higher hit.")
    _print("  ⚠️ Exploratory; selection + overlap; sign-free (CE+PE pooled).")


if __name__ == "__main__":
    main()
