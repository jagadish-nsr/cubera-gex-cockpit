"""Daily straddle vol-timing around the opening GEX regime.

The one confirmed edge is |GEX| → forward realized vol, and it survives the ATM-IV partial (Study
#1) — i.e. |GEX| carries vol info BEYOND what IV prices. If real, IV mis-prices the straddle by
regime, so: HIGH |GEX| at the open → realized vol < implied → SELL the straddle; LOW |GEX| →
realized > implied → BUY it. This tests it directly.

Per day: at ENTRY (09:20) take the ATM straddle (CE+PE at the nearest strike) and the opening
|GEX| regime (tercile vs full history); hold to EXIT (15:25, same strikes); P&L = premium change.
Report P&L, win-rate, realized move vs straddle breakeven, and ATM IV by |GEX| tercile — and a
simple GEX-timed short-vol strategy vs an unconditional one.

  python scripts/straddle_gex_timing.py            # NIFTY  [entry]  [exit]  [cost%]

⚠️ Overlapping days autocorrelate; theta ∝ DTE (reported); costs are a rough round-trip %. Exploratory."""

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


def _straddle(df, stamp, K=None):
    """(strike, CE+PE premium, spot) at a stamp. If K is None use the ATM strike; else value the
    GIVEN strike K (so exit values the SAME strikes bought at entry — the correct straddle P&L,
    which keeps trending days where a leg goes ITM). None if unavailable."""
    s = df[df["time_ist"] == stamp]
    if s.empty:
        return None
    spot = float(s["spot"].iloc[0])
    ce = s[s["side"] == "CE"]
    pe = s[s["side"] == "PE"]
    if ce.empty or pe.empty:
        return None
    if K is None:
        K = float(ce["strike"].iloc[(ce["strike"] - spot).abs().argmin()])
    c = ce[ce["strike"] == K]["close"]
    p = pe[pe["strike"] == K]["close"]
    if c.empty or p.empty or c.iloc[0] < 0 or p.iloc[0] < 0:
        return None
    return K, float(c.iloc[0]) + float(p.iloc[0]), spot


def main() -> None:
    import numpy as np
    import pandas as pd
    from data.dhan import load_intraday_day, available_intraday_days
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    entry = sys.argv[2] if len(sys.argv) > 2 else "09:20"
    exit_ = sys.argv[3] if len(sys.argv) > 3 else "15:25"
    cost = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0

    feat = load_features(store)
    # opening |GEX|, atm_iv, dte per day at the entry stamp
    fe = feat[feat["time_ist"] == entry].set_index("day")
    rows = []
    for day in available_intraday_days(store):
        if day not in fe.index:
            continue
        df = load_intraday_day(store, day)
        if df is None:
            continue
        en = _straddle(df, entry)                            # ATM straddle at entry
        if en is None or en[1] <= 0:
            continue
        k, prem0, sp0 = en
        ex = _straddle(df, exit_, K=k)                       # value the SAME strikes at exit
        if ex is None:
            continue
        _, prem1, sp1 = ex
        rows.append({"day": day, "absg": abs(float(fe.loc[day, "net_gex"])),
                     "iv": float(fe.loc[day, "atm_iv"]), "dte": float(fe.loc[day, "dte"]),
                     "long_pnl": (prem1 / prem0 - 1) * 100, "cost_pct": prem0 / sp0 * 100,
                     "move_pct": abs(sp1 - sp0) / sp0 * 100})
    df = pd.DataFrame(rows)
    if len(df) < 60:
        _print(f"Only {len(df)} days.")
        return
    df["moveBE"] = df["move_pct"] / df["cost_pct"]          # realized move ÷ breakeven (>1 long wins)
    q1, q2 = df["absg"].quantile([1 / 3, 2 / 3])
    df["reg"] = np.where(df["absg"] <= q1, "LOW |GEX|", np.where(df["absg"] >= q2, "HIGH |GEX|", "MID"))

    _print(f"Daily straddle vol-timing by opening GEX regime · {store} · {len(df)} days · "
           f"entry {entry} → exit {exit_} · cost {cost:.1f}%\n")
    _print("RAW by |GEX| tercile (CONFOUNDED by DTE — note the dte column):")
    _print(f"  {'regime':<10} {'n':>4} {'longP&L%':>9} {'win':>5} {'move/BE':>8} {'atmIV':>6} {'dte':>4}")
    for r in ("LOW |GEX|", "MID", "HIGH |GEX|"):
        g = df[df["reg"] == r]
        _print(f"  {r:<10} {len(g):>4} {g['long_pnl'].mean():>+8.2f}% {100*(g['long_pnl']>0).mean():>4.0f}% "
               f"{g['moveBE'].mean():>8.2f} {g['iv'].mean()*100:>5.1f}% {g['dte'].mean():>4.1f}")

    # DECISIVE CONTROL: within a DTE band (theta held ~fixed), does |GEX| still separate the
    # straddle P&L / realized-vs-implied? If LOW≈HIGH within each band, the regime effect was DTE.
    df["dband"] = pd.cut(df["dte"], [-1, 1.5, 3, 5, 99], labels=["≤1.5d", "1.5-3d", "3-5d", ">5d"])
    _print("\nDTE-CONTROLLED — within each DTE band, |GEX| below vs above the band median:")
    _print(f"  {'DTE band':<8} {'|GEX|':>5} {'n':>4} {'longP&L%':>9} {'shortP&L%':>10} "
           f"{'move/BE':>8} {'atmIV':>6}")
    for b in ["≤1.5d", "1.5-3d", "3-5d", ">5d"]:
        sub = df[df["dband"] == b]
        if len(sub) < 20:
            continue
        med = sub["absg"].median()
        for tag, m in [("LOW", sub["absg"] <= med), ("HIGH", sub["absg"] > med)]:
            g = sub[m]
            if g.empty:
                continue
            _print(f"  {b:<8} {tag:>5} {len(g):>4} {g['long_pnl'].mean():>+8.2f}% "
                   f"{(-g['long_pnl'] - cost).mean():>+9.2f}% {g['moveBE'].mean():>8.2f} "
                   f"{g['iv'].mean()*100:>5.1f}%")
    _print("\n  Read: a real GEX vol-edge = within a DTE band, HIGH-|GEX| days have LOWER move/BE and a "
           "MORE-positive short-straddle P&L than LOW-|GEX| days. If the two rows match within each band, "
           "|GEX| adds nothing beyond DTE/theta — IV already prices the GEX-vol link.")
    _print("  ⚠️ Overlapping/autocorrelated; entry=ATM held to close; rough costs. Exploratory.")


if __name__ == "__main__":
    main()
