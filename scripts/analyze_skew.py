"""Option SKEW (OTM put richness vs OTM call) → forward Nifty return.

Study #8 (docs/research-agenda.md §8; Xing-Zhang-Zhao). A steep put skew = informed
downside demand → US finding: predicts LOWER future returns. We measure a robust,
rank-equivalent proxy from EOD premiums (avoids fragile IV inversion on settlement prices):

  skew = OTM-put premium / OTM-call premium, at ~equidistant OTM strikes (nearest expiry).

Higher = puts richer than equidistant calls = steeper put skew (downside fear/hedging).
Point-in-time: skew at day t vs close-to-close return t→t+h. Tested on 2+ years of the raw
F&O bhavcopy. India (writer-dominated) may flip the US sign — we report and read it.

  python scripts/analyze_skew.py            # NIFTY

⚠️ Exploratory; overlapping horizons autocorrelate. Sign-free target. Not a trade rule."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bhavcopy_raw import available_days, load_raw_fo_day  # noqa: E402

HORIZONS = [1, 2, 5]
OTM_FRAC = 0.03                  # ~3% OTM each side (near-money, liquid)
MIN_DTE = 2                      # skip <2-day expiries (tiny/degenerate premiums)


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _nearest_row(frame, target):
    """Row whose strike is nearest ``target`` (with a positive close), or None."""
    f = frame[(frame["close"] > 0)]
    if f.empty:
        return None
    return f.iloc[(f["strike"] - target).abs().argmin()]


def _day_skew(day: str, symbol: str) -> dict | None:
    df = load_raw_fo_day(day, symbol)
    if df is None:
        return None
    opt = df[df["instr"] == "IDO"].copy()
    if opt.empty or opt["expiry"].isna().all():
        return None
    spot = float(pd.to_numeric(df["spot"], errors="coerce").dropna().iloc[0])
    # nearest expiry that is >= MIN_DTE days out
    d0 = date.fromisoformat(day)
    exps = sorted(opt["expiry"].dropna().unique())
    exp = next((e for e in exps if (date.fromisoformat(e) - d0).days >= MIN_DTE), exps[-1] if exps else None)
    if exp is None:
        return None
    near = opt[opt["expiry"] == exp]
    put = _nearest_row(near[near["opt"] == "PE"], spot * (1 - OTM_FRAC))   # OTM put (K<S)
    call = _nearest_row(near[near["opt"] == "CE"], spot * (1 + OTM_FRAC))  # OTM call (K>S)
    if put is None or call is None or call["close"] <= 0:
        return None
    skew = float(put["close"] / call["close"])
    return {"day": day, "spot": spot, "skew": skew}


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    days = available_days(symbol)
    sig = [s for s in (_day_skew(d, symbol) for d in days) if s]
    if len(sig) < 30:
        _print(f"Only {len(sig)} days — run scripts/backfill_bhavcopy_raw.py first.")
        return
    df = pd.DataFrame(sig).reset_index(drop=True)
    spot = df["spot"].to_numpy()
    for h in HORIZONS:
        fwd = np.full(len(df), np.nan)
        for i in range(len(df) - h):
            fwd[i] = (spot[i + h] / spot[i] - 1.0) * 100.0
        df[f"fwd_{h}"] = fwd

    _print(f"OTM put/call skew → forward Nifty return  ·  {symbol}  ·  {len(df)} days "
           f"({df['day'].iloc[0]} … {df['day'].iloc[-1]})\n")
    _print("Spearman IC (skew at t vs t→t+h return):")
    for h in HORIZONS:
        s = df[["skew", f"fwd_{h}"]].dropna()
        ic = s["skew"].corr(s[f"fwd_{h}"], method="spearman") if len(s) >= 10 else float("nan")
        _print(f"  {h}d:  IC {ic:+.2f}   (n={len(s)})")
    _print(f"\n  skew median {df['skew'].median():.2f} (put/call prem). US read: steep skew ⇒ "
           "LOWER returns (NEGATIVE IC). Overlapping horizons autocorrelate — read the sign.")
    _print("\n⚠️ EXPLORATORY — premium-ratio skew proxy on EOD prices; sign-free target. Not a trade rule.")


if __name__ == "__main__":
    main()
