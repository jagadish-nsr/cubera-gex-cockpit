"""Historical dealer GEX → forward realized volatility (SqueezeMetrics, 2+ years).

The properly-powered version of study #1 (which had 19 days of live net_gex; docs/
research-agenda.md §3). Recompute daily net GEX from the raw bhavcopy (per-strike gamma ×
OI) over 2+ years and test it against forward realized vol. SqueezeMetrics: dealer gamma is
inversely related to RV (short gamma amplifies, long gamma dampens).

Per day (nearest expiry ≥ MIN_DTE): one ATM implied vol (from the ATM call), per-strike BS
gamma at that IV, net GEX = Σ gamma·(callOI − putOI) [model A dealer sign]. Forward RV(h) =
std of the next h daily log-returns. Point-in-time: GEX at t, RV over (t, t+h].

  |GEX|  → RV : gamma CONCENTRATION (SIGN-FREE)
  GEX    → RV : signed. NEG ⇒ +gamma calmer (US model A); POS ⇒ +gamma WILDER ⇒ inverted
                India sign (model B) — a dealer-sign read (cf. both-signs work).

  python scripts/analyze_gex_vol.py            # NIFTY

⚠️ Exploratory. EOD-bhavcopy GEX is approximate (one ATM IV/day, r=q=0). Overlapping RV
windows autocorrelate. Sign-free for |GEX|. Not a trade rule."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bhavcopy_raw import available_days, load_raw_fo_day  # noqa: E402
from app.gex_history import compute_day_gex  # noqa: E402

HORIZONS = [5, 10]


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _day_gex(day: str, symbol: str) -> dict | None:
    df = load_raw_fo_day(day, symbol)
    if df is None:
        return None
    spot = pd.to_numeric(df["spot"], errors="coerce").dropna()
    if spot.empty:
        return None
    g_smile = compute_day_gex(day, symbol, smile=True)      # per-strike IV (the smile)
    g_flat = compute_day_gex(day, symbol, smile=False)      # one ATM IV (crude)
    if g_smile is None or g_flat is None:
        return None
    return {"day": day, "spot": float(spot.iloc[0]),
            "gex": g_smile, "absgex": abs(g_smile),
            "gex_flat": g_flat, "absgex_flat": abs(g_flat)}


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    days = available_days(symbol)
    sig = [s for s in (_day_gex(d, symbol) for d in days) if s]
    if len(sig) < 30:
        _print(f"Only {len(sig)} usable days — run scripts/backfill_bhavcopy_raw.py first.")
        return
    df = pd.DataFrame(sig).reset_index(drop=True)
    lr = np.log(df["spot"].to_numpy())
    dret = np.diff(lr, prepend=np.nan)          # daily log return
    for h in HORIZONS:
        rv = np.full(len(df), np.nan)
        for i in range(len(df) - h):
            rv[i] = np.nanstd(dret[i + 1:i + 1 + h]) * 100
        df[f"rv_{h}"] = rv

    _print(f"Historical dealer GEX → forward realized vol  ·  {symbol}  ·  {len(df)} days "
           f"({df['day'].iloc[0]} … {df['day'].iloc[-1]})\n")
    _print("Spearman IC vs forward RV  [per-strike-IV smile | flat ATM-IV]:")
    for h in HORIZONS:
        def _ic(col):
            s = df[[col, f"rv_{h}"]].dropna()
            return s[col].corr(s[f"rv_{h}"], method="spearman"), len(s)
        ica, n = _ic("absgex"); icaf, _ = _ic("absgex_flat")
        ics, _ = _ic("gex"); icsf, _ = _ic("gex_flat")
        _print(f"  {h}d (n={n}):  |GEX| {ica:+.2f}|{icaf:+.2f}    signed {ics:+.2f}|{icsf:+.2f}")
    _print("\n  |GEX|→RV sign-free (concentration; NEG = concentration dampens vol, the "
           "SqueezeMetrics pattern). signed: NEG ⇒ +gamma calmer (US model A), POS ⇒ inverted (B).")
    _print("\n⚠️ EXPLORATORY — approximate EOD GEX (one ATM IV/day, r=q=0); overlapping RV "
           "windows autocorrelate. Sign-free for |GEX|. Not a trade rule.")


if __name__ == "__main__":
    main()
