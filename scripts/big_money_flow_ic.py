"""Does the big-money FLOW LEAN predict forward returns? ([W2]) — the validation harness.

Pre-registered test: for each captured session, take the day's net signed-flow lean (buy- minus
sell-initiated ₹, from the SQLite event store) and correlate it with the forward return to the NEXT
captured session's close (from the raw-tick archive's own last price — self-contained, no external
series). Spearman IC across days, per symbol, vs a shuffled-return MIRROR placebo, with n + p.

This is the honest scorecard for the dashboard's "flow bias" block: it stays DESCRIPTIVE until this
shows a real, placebo-beating IC on enough sessions. Point-in-time (the lean is known at the close it
is measured at; the return is strictly after). Sign-free of the dealer landmine. Decision-support.

  python scripts/big_money_flow_ic.py            # all symbols on record

⚠️ Needs a real sample. Until ~15+ captured sessions exist it will report INSUFFICIENT — by design.
It also tests only the COARSE daily lean; an intraday (first-half → second-half) test is a future
refinement once sessions accrue."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.big_money_db import daily_rollup                 # noqa: E402
from app.big_money_live import DEFAULT_SYMBOLS, load_raw_ticks   # noqa: E402

MIN_N = 15                                                 # fewer sessions than this = not testable


def _print(s: str = "") -> None:
    print(s, flush=True)


def _close(symbol: str, day: str):
    """The day's closing price from the raw-tick archive (last non-null ltp), or None."""
    import pandas as pd
    df = load_raw_ticks(symbol, day)
    if df is None or df.empty or "ltp" not in df.columns:
        return None
    s = pd.to_numeric(df["ltp"], errors="coerce").dropna()
    return float(s.iloc[-1]) if len(s) else None


def _symbol_ic(symbol: str) -> None:
    import numpy as np
    from scipy.stats import spearmanr

    roll = {r["day"]: r["net_pct"] for r in daily_rollup(symbols=[symbol])}
    days = sorted(roll)
    if len(days) < 2:
        _print(f"  {symbol:<10} INSUFFICIENT — {len(days)} session(s) with events on record.")
        return
    lean, fwd = [], []
    for d, dn in zip(days, days[1:]):                      # forward return to the NEXT captured session
        c0, c1 = _close(symbol, d), _close(symbol, dn)
        if c0 and c1:
            lean.append(roll[d])
            fwd.append((c1 / c0 - 1.0) * 100.0)
    n = len(lean)
    if n < MIN_N:
        _print(f"  {symbol:<10} INSUFFICIENT — {n} usable day-pair(s) (need ≥{MIN_N}). "
               f"Keep collecting; re-run later.")
        return
    ic, p = spearmanr(lean, fwd)
    rng = np.random.default_rng(0)
    placebo = np.mean([abs(spearmanr(lean, rng.permutation(fwd))[0]) for _ in range(500)])
    verdict = ("SURVIVES (beats placebo)" if abs(ic) > 2 * placebo and p < 0.05
               else "no edge (indistinguishable from the mirror)")
    _print(f"  {symbol:<10} n={n}  Spearman IC={ic:+.2f} (p={p:.2f})  |mirror|≈{placebo:.2f}  → {verdict}")


def main() -> None:
    _print("Big-money flow-lean → forward-return IC  ·  daily net-flow lean vs next-session return\n")
    for sym in DEFAULT_SYMBOLS:
        _symbol_ic(sym)
    _print("\n  Read: the flow bias earns a directional claim only if IC clearly beats the |mirror| "
           "AND p<0.05 on a real sample. Until then it stays a DESCRIPTIVE readout. Coarse daily test; "
           "intraday split is a future refinement. Sign-free; decision-support, not a trade rule.")


if __name__ == "__main__":
    main()
