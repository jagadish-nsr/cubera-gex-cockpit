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
from gex.bs import bs_gamma, implied_vol_call  # noqa: E402

HORIZONS = [5, 10]
MIN_DTE = 3
BAND_FRAC = 0.10                 # strikes within ±10% of spot contribute to GEX


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
    opt = df[df["instr"] == "IDO"].copy()
    if opt.empty or opt["expiry"].isna().all():
        return None
    spot = float(pd.to_numeric(df["spot"], errors="coerce").dropna().iloc[0])
    d0 = date.fromisoformat(day)
    exps = sorted(opt["expiry"].dropna().unique())
    exp = next((e for e in exps if (date.fromisoformat(e) - d0).days >= MIN_DTE), None)
    if exp is None:
        return None
    T = (date.fromisoformat(exp) - d0).days / 365.0
    near = opt[opt["expiry"] == exp]
    # ATM IV from the ATM call
    ce = near[(near["opt"] == "CE") & (near["close"] > 0)]
    if ce.empty:
        return None
    atm = ce.iloc[(ce["strike"] - spot).abs().argmin()]
    iv = implied_vol_call(float(atm["close"]), spot, float(atm["strike"]), T)
    if not (iv and iv == iv) or iv <= 0:
        return None
    # net GEX = Σ gamma·(callOI − putOI) over near-money strikes (model A dealer sign)
    gex = 0.0
    for k, g in near.groupby("strike"):
        k = float(k)
        if abs(k - spot) > spot * BAND_FRAC:
            continue
        gam = bs_gamma(spot, k, T, iv)
        coi = float(g[g["opt"] == "CE"]["oi"].sum())
        poi = float(g[g["opt"] == "PE"]["oi"].sum())
        gex += gam * (coi - poi)
    gex *= spot * spot * 0.01           # standard $-gamma-per-1% scaling (units arbitrary for IC)
    return {"day": day, "spot": spot, "gex": gex, "absgex": abs(gex)}


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
    _print("Spearman IC vs forward RV:")
    for h in HORIZONS:
        a = df[["absgex", f"rv_{h}"]].dropna()
        s = df[["gex", f"rv_{h}"]].dropna()
        ica = a["absgex"].corr(a[f"rv_{h}"], method="spearman")
        ics = s["gex"].corr(s[f"rv_{h}"], method="spearman")
        _print(f"  {h}d:  |GEX| {ica:+.2f}   signed GEX {ics:+.2f}   (n={len(a)})")
    _print("\n  |GEX|→RV sign-free (concentration). signed: NEG ⇒ +gamma calmer (US model A); "
           "POS ⇒ +gamma wilder ⇒ inverted India sign (model B).")
    _print("\n⚠️ EXPLORATORY — approximate EOD GEX (one ATM IV/day, r=q=0); overlapping RV "
           "windows autocorrelate. Sign-free for |GEX|. Not a trade rule.")


if __name__ == "__main__":
    main()
