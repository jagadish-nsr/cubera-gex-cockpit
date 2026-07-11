"""Put-Call Ratio (OI and volume) → forward Nifty return at multiple horizons.

Study #6 (docs/research-agenda.md §6). The one result the NSE-Nifty literature (MDPI
Economies, 2001–2013) reported as REAL: OI-PCR predicts at ~12 days, volume-PCR at ~2.5
days. India nuance (writer-dominated): high PCR = heavy put-WRITING = support/bullish, so
we'd expect a POSITIVE IC (high PCR → higher forward return) — the opposite of the US
"high PCR = fear" read. We test that directly, at multiple horizons, on 2+ years of the
raw F&O bhavcopy (full-chain OI/volume + spot).

Point-in-time: PCR at day t vs the close-to-close return t→t+h (h in trading days).

  python scripts/analyze_pcr_horizons.py            # NIFTY

⚠️ Exploratory. Multi-day horizons OVERLAP heavily → effective independent n ≈ (days/h);
Spearman here is directional evidence, not a backtest. Sign-free (raw OI/volume). Not a
trade rule."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bhavcopy_raw import available_days, load_raw_fo_day  # noqa: E402

HORIZONS = [1, 2, 5, 12]          # trading days ahead


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _day_pcr(day: str, symbol: str) -> dict | None:
    df = load_raw_fo_day(day, symbol)
    if df is None:
        return None
    opt = df[df["instr"] == "IDO"]
    if opt.empty:
        return None
    spot = float(pd.to_numeric(df["spot"], errors="coerce").dropna().iloc[0])
    near = opt[opt["expiry"] == sorted(opt["expiry"].dropna().unique())[0]] if opt["expiry"].notna().any() else opt

    def _pcr(frame, col):
        c = frame[frame["opt"] == "CE"][col].sum()
        p = frame[frame["opt"] == "PE"][col].sum()
        return float(p / c) if c else np.nan

    return {"day": day, "spot": spot,
            "pcr_oi": _pcr(opt, "oi"), "pcr_vol": _pcr(opt, "volume"),
            "pcr_oi_near": _pcr(near, "oi")}


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    days = available_days(symbol)
    sig = [s for s in (_day_pcr(d, symbol) for d in days) if s]
    if len(sig) < 30:
        _print(f"Only {len(sig)} days — run scripts/backfill_bhavcopy_raw.py first.")
        return
    df = pd.DataFrame(sig).reset_index(drop=True)
    spot = df["spot"].to_numpy()
    # forward close-to-close return at h TRADING days ahead
    for h in HORIZONS:
        fwd = np.full(len(df), np.nan)
        for i in range(len(df) - h):
            fwd[i] = (spot[i + h] / spot[i] - 1.0) * 100.0
        df[f"fwd_{h}"] = fwd

    _print(f"PCR → forward Nifty return  (point-in-time)  ·  {symbol}  ·  {len(df)} days "
           f"({df['day'].iloc[0]} … {df['day'].iloc[-1]})\n")
    _print("Spearman IC (PCR at t vs t→t+h return):")
    _print(f"  {'signal':16s}" + "".join(f"  {str(h)+'d':>7}" for h in HORIZONS))
    for col, lab in [("pcr_oi", "PCR-OI (full)"), ("pcr_oi_near", "PCR-OI (near-exp)"),
                     ("pcr_vol", "PCR-volume")]:
        cells = []
        for h in HORIZONS:
            s = df[[col, f"fwd_{h}"]].dropna()
            ic = s[col].corr(s[f"fwd_{h}"], method="spearman") if len(s) >= 10 else float("nan")
            cells.append(f"{ic:+.2f}")
        _print(f"  {lab:16s}" + "".join(f"  {c:>7}" for c in cells))
    _print(f"\n  eff. independent n ≈ days/h  (e.g. {len(df)}/12 ≈ {len(df)//12}). India read: "
           "POSITIVE IC ⇒ high PCR (put-writing) = bullish (writer-dominated), per the NSE lit.")
    _print("\n⚠️ EXPLORATORY — overlapping multi-day windows autocorrelate heavily; read SIGN + "
           "shape across horizons, not magnitude. Sign-free. Not a trade rule.")


if __name__ == "__main__":
    main()
