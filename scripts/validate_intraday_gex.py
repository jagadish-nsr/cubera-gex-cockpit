"""Pipeline sanity: does the intraday 5-min |GEX| agree with the true-expiry EOD |GEX|?

The intraday feature cache backs out time-to-expiry calendar-free (BS-inversion on the open ATM
call, r=q=0) and uses Dhan's per-strike IV over the ATM±10 band. The EOD app/gex_history uses the
TRUE bhavcopy expiry, parity-smile IV, and a ±10% band. If the intraday build is sound, the
last-5-min-stamp |GEX| should rank-agree with the EOD |GEX| on overlapping days (Spearman).
A high rank correlation means the T-approximation and IV handling are fine for a sign-free study.

  python scripts/validate_intraday_gex.py            # NIFTY

Reports n, Spearman/Pearson of |GEX| and of spot. Not a trade study — a correctness gate."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    import numpy as np
    import pandas as pd
    import app.gex_history as gh
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    feat = load_features(store)
    if feat is None:
        _print("No intraday feature cache — build it first (app.intraday_features.build).")
        return
    last = feat.sort_values("time_ist").groupby("day").tail(1).set_index("day")
    # Recompute EOD |GEX| APPLES-TO-APPLES with the intraday build: nearest expiry (incl weekly,
    # MIN_DTE=0) and the same ~±2% near-money band. (The default gex_history uses ±10% + MIN_DTE≥3,
    # a genuinely different measure — comparing to that understates agreement.)
    gh.MIN_DTE = 0
    gh.BAND_FRAC = 0.021
    from app.bhavcopy_raw import available_days
    bh = set(available_days(store))
    days = [d for d in last.index if d in bh]
    eod = {}
    for d in days:
        g = gh.compute_day_gex(d, store)
        if g is not None:
            eod[d] = g
    days = [d for d in days if d in eod]
    if len(days) < 30:
        _print(f"Only {len(days)} overlapping days — need the EOD gex_history cache too.")
        return
    intr = np.array([abs(float(last.loc[d, "net_gex"])) for d in days])
    eodg = np.array([abs(float(eod[d])) for d in days])
    intr_s = pd.Series(intr)
    _print(f"Intraday |GEX| vs EOD true-expiry |GEX|  ·  {store}  ·  {len(days)} overlapping days\n")
    _print(f"  |GEX|  Spearman {intr_s.corr(pd.Series(eodg), method='spearman'):+.3f}   "
           f"Pearson {intr_s.corr(pd.Series(eodg)):+.3f}")
    _print(f"  intraday |GEX| median {np.median(intr):,.0f}   EOD median {np.median(eodg):,.0f}")
    _print("\n  (High Spearman ⇒ the calendar-free T + Dhan-IV intraday build ranks days the same "
           "as the true-expiry EOD engine — the pipeline is sound for sign-free |GEX| studies.)")


if __name__ == "__main__":
    main()
