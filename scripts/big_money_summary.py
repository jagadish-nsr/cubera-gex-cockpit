"""Big-money daily summary ([W2]) — sanity-check a day's capture AND calibrate the ₹ floors from the
ACTUAL distribution, plus a signed order-flow lean (the 'direction big money leaned').

Reads the raw tick archive (data/journal/big_money_raw/<SYM>/<day>.csv) + the big-money event store,
and per symbol reports: tick count & span, the single-print notional distribution (→ a suggested PRINT
floor), the 10s-window traded-notional distribution (→ a suggested BURST floor), the biggest prints,
and buy- vs sell-initiated traded notional (Lee-Ready signed flow). Point-in-time; sign-free of the
dealer landmine (aggressor side comes from the live bid/ask, not a model). Decision-support.

  python scripts/big_money_summary.py                 # today, all symbols
  python scripts/big_money_summary.py 2026-07-23       # a specific day
  python scripts/big_money_summary.py 2026-07-23 NIFTY # one symbol
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.big_money import IST, classify_aggressor, load_events    # noqa: E402
from app.big_money_live import DEFAULT_SYMBOLS, load_raw_ticks    # noqa: E402
WINDOW_S = 10                                                     # burst window for the calibration


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _cr(x) -> str:
    return f"₹{x/1e7:.2f}cr"


def _round_floor(x: float) -> float:
    """Round a suggested ₹ floor to a friendly figure (nearest 25L up to ₹2cr, else nearest 50L)."""
    step = 25e5 if x < 2e7 else 50e5
    return max(step, round(x / step) * step)


def _summarise(symbol: str, day: str) -> None:
    import numpy as np
    import pandas as pd

    df = load_raw_ticks(symbol, day)
    evs = [e for e in load_events(symbol, day)]
    if df is None or df.empty:
        _print(f"\n{symbol}: no raw ticks archived for {day}.  (events: {len(evs)})")
        return
    df = df.sort_values("epoch").reset_index(drop=True)
    df["ltp"] = pd.to_numeric(df["ltp"], errors="coerce")
    df["ltq"] = pd.to_numeric(df["ltq"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    n, t0, t1 = len(df), df["time_ist"].iloc[0], df["time_ist"].iloc[-1]
    full = int((df["code"] == 8).sum())
    _print(f"\n{symbol}  ·  {n:,} ticks  ·  {t0} → {t1}  ·  Full {100*full/n:.0f}% / Quote {100*(n-full)/n:.0f}%")

    # single-print notional distribution (each snapshot's last trade) → PRINT floor
    pn = (df["ltq"] * df["ltp"]).dropna()
    if len(pn):
        q = pn.quantile([.5, .9, .95, .99])
        _print(f"  print notional : p50 {_cr(q[.5])}  p90 {_cr(q[.9])}  p95 {_cr(q[.95])}  "
               f"p99 {_cr(q[.99])}  max {_cr(pn.max())}")
        _print(f"     → suggested PRINT floor ≈ {_cr(_round_floor(q[.99]))} (p99)")

    # signed order flow from consecutive-volume deltas classified by aggressor (the directional lean)
    dvol = df["volume"].diff().clip(lower=0).fillna(0.0)
    notl = dvol * df["ltp"]
    side = [classify_aggressor(l, b, a) for l, b, a in
            zip(df["ltp"], pd.to_numeric(df.get("bid"), errors="coerce"),
                pd.to_numeric(df.get("ask"), errors="coerce"))]
    s = pd.Series(side)
    buy = float(notl[s.values == "buy"].sum())
    sell = float(notl[s.values == "sell"].sum())
    mid = float(notl[s.isna().values].sum())
    tot = buy + sell + mid
    if tot > 0:
        net = (buy - sell) / tot * 100
        lean = "BUY" if net > 0 else "SELL"
        _print(f"  signed flow    : buy {_cr(buy)} ({100*buy/tot:.0f}%)  sell {_cr(sell)} "
               f"({100*sell/tot:.0f}%)  mid {100*mid/tot:.0f}%   → net {net:+.0f}% ({lean}-leaning)")

    # 10s-window traded notional → BURST floor
    if df["epoch"].notna().any():
        win = notl.groupby((df["epoch"] // WINDOW_S)).sum()
        win = win[win > 0]
        if len(win):
            wq = win.quantile([.5, .9, .99])
            _print(f"  {WINDOW_S}s-window flow : p50 {_cr(wq[.5])}  p90 {_cr(wq[.9])}  p99 {_cr(wq[.99])}  "
                   f"max {_cr(win.max())}")
            _print(f"     → suggested BURST floor ≈ {_cr(_round_floor(wq[.99]))} (p99)")

    # biggest single prints
    top = df.assign(notl=df["ltq"] * df["ltp"]).nlargest(5, "notl")
    if len(top):
        _print("  biggest prints :")
        for r in top.itertuples():
            sd = classify_aggressor(r.ltp, getattr(r, "bid", None), getattr(r, "ask", None))
            tag = {"buy": "▲BUY ", "sell": "▼SELL", None: "  •  "}[sd]
            _print(f"     {r.time_ist}  {tag}  {r.ltq:>6.0f}u  {_cr(r.notl)}  @{r.ltp:.1f}")

    # what actually fired into the event store
    if evs:
        kinds = pd.Series([e.get("kind") for e in evs]).value_counts().to_dict()
        sides = pd.Series([e.get("side") for e in evs]).value_counts(dropna=False).to_dict()
        _print(f"  events fired   : {len(evs)}  {kinds}  sides {sides}")
    else:
        _print("  events fired   : 0 (no print/burst cleared the floor today)")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    day = next((a for a in args if a.count("-") == 2), datetime.now(IST).strftime("%Y-%m-%d"))
    syms = [a.upper() for a in args if a.count("-") != 2] or list(DEFAULT_SYMBOLS)
    _print(f"Big-money day summary · {day} · window {WINDOW_S}s")
    for sym in syms:
        _summarise(sym, day)
    _print("\n  Floors are placeholders — set BIG_PRINT_FLOOR / BURST_FLOOR (app/big_money.py) near the "
           "p99 suggestions above once you have a full, representative session. Sign-free; decision-support.")


if __name__ == "__main__":
    main()
