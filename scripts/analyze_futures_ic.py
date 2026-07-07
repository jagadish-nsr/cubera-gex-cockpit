"""Does today's Nifty-futures OI build-up predict the NEXT day's return?

Point-in-time IC on the daily futures build-up (from the F&O bhavcopy journal). At each
day t's close the build-up state is known (ΔPrice + aggregate ΔOI, rollover-netted); we
measure the next trading day's spot return t→t+1. The build-up BIAS sign equals
sign(ΔPrice), so a naive bias IC just re-measures daily momentum — the real question is
whether the OI dimension (fresh money vs position-squaring) ADDS anything, so we also:

  - split the four build-up states and compare their next-day returns (esp. long build-up
    vs short covering — both are UP days, but one has fresh longs, the other just shorts
    exiting), and
  - test a "fresh-money only" signal (+1 long build-up / −1 short build-up / 0 covering
    or unwinding) against the momentum baseline.

  python scripts/analyze_futures_ic.py            # NIFTY
  python scripts/analyze_futures_ic.py BANKNIFTY

⚠️ Exploratory, decision-support only. Tiny n (a few weeks of daily bars). Read the SIGN
and per-state hit-rate, never the decimals. Not a trade rule. Sign-free (futures OI+price).
Next-day return uses the bhavcopy underlying (spot) close-to-close; pairs spanning a data
gap > 4 calendar days are flagged and excluded."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.futures_buildup import buildup_bias, classify_futures_buildup, futures_series  # noqa: E402


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _gap_days(d0: str, d1: str) -> int:
    return (datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d0, "%Y-%m-%d")).days


def _bias_sign(label):
    b = buildup_bias(label)
    return 1 if b and b.startswith("bull") else -1 if b and b.startswith("bear") else 0


_FRESH = {"long build-up": 1, "short build-up": -1, "short covering": 0, "long unwinding": 0}


def build_pairs(symbol: str) -> pd.DataFrame:
    ser = futures_series(symbol, n=0)                 # all captured days, chronological
    rows = []
    for i in range(len(ser) - 1):
        a, b = ser[i], ser[i + 1]
        sa, sb = a.get("spot"), b.get("spot")
        if not sa or not sb:
            continue
        label = a.get("agg_buildup")
        rows.append({
            "date": a["date"], "next": b["date"],
            "gap": _gap_days(a["date"], b["date"]),
            "dprice": a.get("dprice"), "agg_doi": a.get("agg_doi"),
            "state": label, "bias": _bias_sign(label), "fresh": _FRESH.get(label, 0),
            "mom": np.sign(a.get("dprice") or 0.0),
            "next_ret": (sb / sa - 1.0) * 100.0,       # % next-day spot return
        })
    df = pd.DataFrame(rows)
    return df[df["gap"] <= 4] if not df.empty else df   # drop pairs across long gaps


def _ic(a, b):
    m = a.notna() & b.notna()
    return (a[m].corr(b[m], method="spearman"), int(m.sum())) if m.sum() >= 5 else (float("nan"), int(m.sum()))


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    df = build_pairs(symbol)
    _print(f"Daily futures build-up  ->  NEXT-day spot return   ·   {symbol}\n")
    _print(f"Usable day pairs: {len(df)}")
    if len(df) < 5:
        _print("Too few pairs for an IC — backfill more days "
               "(scripts/log_futures_buildup.py --backfill N), then re-run.")
        return

    ic_mom, n = _ic(df["mom"], df["next_ret"])
    ic_fresh, _ = _ic(df["fresh"], df["next_ret"])
    ic_doi, _ = _ic(df["agg_doi"], df["next_ret"])
    _print("\nSpearman IC vs next-day return:")
    _print(f"  momentum  sign(ΔPrice)        IC {ic_mom:+.2f}   (n={n})   [baseline]")
    _print(f"  fresh-money  +1 LB / −1 SB     IC {ic_fresh:+.2f}   (OI-confirmed only)")
    _print(f"  raw aggregate ΔOI             IC {ic_doi:+.2f}")

    _print("\nNext-day return by build-up state (does OI split UP/DOWN days?):")
    order = ["long build-up", "short covering", "short build-up", "long unwinding", "flat"]
    for st in order:
        s = df[df["state"] == st]["next_ret"].dropna()
        if len(s):
            _print(f"  {st:16s} ({str(buildup_bias(st) or '-')[:12]:12s}): "
                   f"mean {s.mean():+.2f}%  median {s.median():+.2f}%  "
                   f"P(up) {(s > 0).mean()*100:.0f}%   (n={len(s)})")

    # The crux: among UP days, do fresh longs (long build-up) outrun short covering?
    up_lb = df[df["state"] == "long build-up"]["next_ret"].dropna()
    up_sc = df[df["state"] == "short covering"]["next_ret"].dropna()
    if len(up_lb) and len(up_sc):
        _print(f"\nUP-day split: long build-up next-ret mean {up_lb.mean():+.2f}% (n={len(up_lb)}) "
               f"vs short covering {up_sc.mean():+.2f}% (n={len(up_sc)}) — "
               f"{'fresh longs continue more' if up_lb.mean() > up_sc.mean() else 'covering not weaker here'}.")

    _print("\n⚠️ EXPLORATORY — tiny n, daily bars, sign-free. Read the SIGN and per-state "
           "hit-rate only; do NOT trade this. The bias sign = sign(ΔPrice) so its IC is just "
           "momentum; the value (if any) is whether OI CONFIRMATION (build-up) beats "
           "position-squaring (covering/unwinding). Re-run as the journal accrues.")


if __name__ == "__main__":
    main()
