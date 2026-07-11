"""Does end-of-day OTM option OI GROWTH predict the next-day Nifty return?

Study #4 from docs/research-agenda.md §1 (Zhou; Seo 2020, index-level). US finding:
growth in OTM (esp. call) option OI predicts higher future returns — informed traders
prefer cheap OTM options, so OI building there leads price. DAILY horizon (the literature's
horizon; our intraday direction studies found nothing).

From each day's bhavcopy EOD per-strike option OI (data/journal/options_eod), we sum the ΔOI (vs prev day) of
near-OTM calls (strike>spot) and near-OTM puts (strike<spot) within ±BAND, and test each
signal against the next trading day's close-to-close return (point-in-time).

⚠️ INDIA SIGN CAVEAT: US results assume buyer-driven OI (OTM-call building = bullish).
India index options are WRITER-dominated, so OTM-call building may be call *writing* =
resistance = BEARISH — the opposite sign. So we DON'T assume a sign; we report the IC and
let the data reveal the microstructure (this doubles as dealer-sign evidence, cf. the
both-signs work). SIGN-of-the-signal is the open question; magnitude/predictivity is the test.

  python scripts/analyze_otm_oi_ic.py            # NIFTY

⚠️ Exploratory — backfilled from the free F&O bhavcopy (months–years of days), overlapping
expiries. Read the SIGN and re-run after backfill_daily.py. Not a trade rule."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.options_eod import available_days, load_options_day, otm_oi_signal  # noqa: E402

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
    rec = load_options_day(day, symbol)          # EOD per-strike from the bhavcopy journal
    if not rec or not rec.get("spot"):
        return None
    sig = otm_oi_signal(rec, band_frac=BAND_FRAC)
    if not sig:
        return None
    return {"day": day, "spot": float(rec["spot"]),
            "otm_call_growth": sig["otm_call_growth"], "otm_put_growth": sig["otm_put_growth"],
            "net_otm": sig["net_otm"] if sig["net_otm"] is not None else np.nan,
            "otm_pcr_doi": sig["otm_pcr_doi"] if sig["otm_pcr_doi"] is not None else np.nan}


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    days = available_days(symbol)
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
    _print(f"signal days: {len(sig)}   ·   usable next-day pairs: {len(df)}")
    if len(df) < 5:
        _print("Too few pairs — run scripts/backfill_daily.py, then re-run.")
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
           "Re-run after backfill_daily.py.")


if __name__ == "__main__":
    main()
