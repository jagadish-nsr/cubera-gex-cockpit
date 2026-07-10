"""Does end-of-day OTM option OI GROWTH predict the next-day Nifty return?

Study #4 from docs/research-agenda.md §1 (Zhou; Seo 2020, index-level). US finding:
growth in OTM (esp. call) option OI predicts higher future returns — informed traders
prefer cheap OTM options, so OI building there leads price. DAILY horizon (the literature's
horizon; our intraday direction studies found nothing).

At each day's LAST oi_strikes snapshot (EOD proxy) we sum the ΔOI (vs prev close) of
near-OTM calls (strike>spot) and near-OTM puts (strike<spot) within ±BAND, and test each
signal against the next trading day's close-to-close return (point-in-time).

⚠️ INDIA SIGN CAVEAT: US results assume buyer-driven OI (OTM-call building = bullish).
India index options are WRITER-dominated, so OTM-call building may be call *writing* =
resistance = BEARISH — the opposite sign. So we DON'T assume a sign; we report the IC and
let the data reveal the microstructure (this doubles as dealer-sign evidence, cf. the
both-signs work). SIGN-of-the-signal is the open question; magnitude/predictivity is the test.

  python scripts/analyze_otm_oi_ic.py            # NIFTY

⚠️ Exploratory — only ~9 oi_strikes days (8 pairs) so far; overlapping expiries; tiny n.
Read the SIGN and re-run as capture accrues. Not a trade rule."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.timeseries import OISTRIKES_DIR, load_strikes_day  # noqa: E402

BAND_FRAC = 0.03                 # near-OTM band = ±3% of spot


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _gap_days(d0, d1):
    return (datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d0, "%Y-%m-%d")).days


def _eod_signal(day: str, symbol: str) -> dict | None:
    df = load_strikes_day(day, symbol)
    if df is None or df.empty or "time_ist" not in df.columns:
        return None
    last = df["time_ist"].max()
    snap = df[df["time_ist"] == last]
    spot = pd.to_numeric(snap["spot"], errors="coerce").dropna()
    if spot.empty:
        return None
    spot = float(spot.iloc[-1])
    band = spot * BAND_FRAC
    k = pd.to_numeric(snap["strike"], errors="coerce")
    cd = pd.to_numeric(snap["call_doi"], errors="coerce")
    pd_ = pd.to_numeric(snap["put_doi"], errors="coerce")
    otm_c = (k > spot) & (k <= spot + band)
    otm_p = (k < spot) & (k >= spot - band)
    call_growth = float(cd[otm_c].clip(lower=0).sum())      # OTM-call OI building (adds only)
    put_growth = float(pd_[otm_p].clip(lower=0).sum())      # OTM-put OI building
    tot = call_growth + put_growth
    return {"day": day, "spot": spot,
            "otm_call_growth": call_growth, "otm_put_growth": put_growth,
            # normalized net OTM call-vs-put building, comparable across days
            "net_otm": (call_growth - put_growth) / tot if tot else np.nan,
            "otm_pcr_doi": put_growth / call_growth if call_growth else np.nan}


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    base = OISTRIKES_DIR / symbol
    days = sorted(p.stem for p in base.glob("*.csv")) if base.exists() else []
    sig = [s for s in (_eod_signal(d, symbol) for d in days) if s]
    # pair each signal-day with the next signal-day's return (close-to-close via last-snap spot)
    rows = []
    for i in range(len(sig) - 1):
        a, b = sig[i], sig[i + 1]
        rows.append({**a, "next": b["day"], "gap": _gap_days(a["day"], b["day"]),
                     "fwd_ret": (b["spot"] / a["spot"] - 1.0) * 100.0})
    df = pd.DataFrame(rows)
    df = df[df["gap"] <= 4] if not df.empty else df

    _print(f"EOD OTM option-OI growth  ->  next-day Nifty return  (point-in-time)  ·  {symbol}\n")
    _print(f"oi_strikes days: {len(sig)}   ·   usable next-day pairs: {len(df)}")
    if len(df) < 5:
        _print("Too few pairs — accrue more oi_strikes days, then re-run.")
        return

    _print("\nSpearman IC vs next-day return:")
    for col, lab in [("net_otm", "net OTM call−put building (norm)"),
                     ("otm_call_growth", "OTM CALL OI growth"),
                     ("otm_put_growth", "OTM PUT OI growth"),
                     ("otm_pcr_doi", "OTM ΔOI put/call ratio")]:
        s = df[[col, "fwd_ret"]].dropna()
        ic = s[col].corr(s["fwd_ret"], method="spearman") if len(s) >= 5 else float("nan")
        _print(f"  {lab:34s}: IC {ic:+.2f}   (n={len(s)})")
    _print("\n  SIGN READ: net_otm/call-growth POSITIVE IC ⇒ OTM-call building = bullish "
           "(US buyer-driven, Zhou). NEGATIVE ⇒ call building = WRITING/resistance = bearish "
           "(India writer-dominated). The sign is itself dealer-microstructure evidence.")
    _print("\n⚠️ EXPLORATORY — tiny n, overlapping expiries, sign-free target. Not a trade rule. "
           "Re-run as oi_strikes days accrue.")


if __name__ == "__main__":
    main()
