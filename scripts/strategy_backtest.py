"""Equity curves (from Rs 10,000) for strategies grounded in the situational-analysis findings.

Honest set — the research says DIRECTION is dead (A1/B/H/I) and the only edge is short-vol/VRP
(tail-risky), barely helped by conditioning (#11b/#18); the vol REGIME persists next-day (#17):

  S1  Short ATM straddle to expiry, EVERY weekly cycle          (the raw VRP edge, #18)
  S2  Short straddle, only when entry IV is HIGH (top tercile)  (sell high IV = the real driver)
  S3  Short straddle, only in SIDEWAYS regime (top |vanna|)     (tests if the #17 regime helps)
  S4  ORB 30-min breakout day-trade (directional)              (Study #16 says ~null — shown for honesty)
  S5  Buy & hold Nifty spot                                     (benchmark)

Sizing (stated, simple, comparable): compound Rs 10,000; per event equity ×= (1 + f·r).
  • straddles: r = pnl%_of_premium × (premium/spot) / MARGIN_RATE  (return on posted margin), f=0.5
  • ORB: r = (close−boundary)/boundary·dir − cost   (unlevered intraday), f=1.0
  • buy-hold: r = spot return, fully invested.
One straddle per weekly cycle (entry = highest-DTE day of the cycle, hold to expiry) → NON-overlapping.
Gross of many real frictions; the SHAPE (growth vs flat, drawdowns) is the point, not the exact rupee.

  python scripts/strategy_backtest.py            # NIFTY   [store]

Writes the equity curves + summary to the scratchpad as JSON (no raw data in the console). Sign-free."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from straddle_gex_timing import _straddle   # noqa: E402

ENTRY = "09:20"
MARGIN_RATE = 0.12
F_OPT = 0.5
STRAD_COST = 2.0        # % of premium, round-trip proxy
ORB_COST = 0.03         # % of notional, intraday round-trip proxy
OPEN = 9 * 60 + 15
ORB_W = 30
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
    r"C:\Users\navee\AppData\Local\Temp\claude\C--Users-navee-Python-gex-indian-gex"
    r"\eb204e31-8169-4259-8f28-affcbf0aa699\scratchpad\strategy_curves.json")


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _curve_stats(dates, eq):
    import numpy as np
    eq = np.asarray(eq, float)
    peak = np.maximum.accumulate(eq)
    maxdd = float(np.max((peak - eq) / peak) * 100) if len(eq) else 0.0
    yrs = (np.datetime64(dates[-1]) - np.datetime64(dates[0])) / np.timedelta64(365, "D") if len(dates) > 1 else 1
    cagr = float((eq[-1] / eq[0]) ** (1 / max(yrs, 1e-6)) - 1) * 100 if len(eq) else 0.0
    return eq[-1], cagr, maxdd


def _orb_daytrade(mins, val):
    import numpy as np
    orm = (mins >= OPEN) & (mins <= OPEN + ORB_W)
    post = mins > OPEN + ORB_W
    if orm.sum() < 8 or post.sum() < 10:
        return None
    orh, orl = float(val[orm].max()), float(val[orm].min())
    rng = orh - orl
    op = float(val[mins <= OPEN + ORB_W][0])
    if rng <= 0:
        return None
    buf = max(0.10 * rng, 3e-4 * op)
    pv = val[post]
    up, dn = np.where(pv > orh + buf)[0], np.where(pv < orl - buf)[0]
    fu = up[0] if len(up) else 10 ** 9
    fd = dn[0] if len(dn) else 10 ** 9
    if min(fu, fd) == 10 ** 9:
        return 0.0                                   # no breakout → flat (cash)
    if fu < fd:
        boundary, d = orh, 1
    else:
        boundary, d = orl, -1
    close = float(val[-1])
    return (close - boundary) / boundary * 100.0 * d - ORB_COST


def main() -> None:
    import numpy as np, pandas as pd
    from data.dhan import load_intraday_day, available_intraday_days
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    feat = load_features(store)
    dte_by = feat.groupby("day")["dte"].first()
    close_spot = feat.sort_values("time_ist").groupby("day")["spot"].last()
    open_spot = feat.sort_values("time_ist").groupby("day")["spot"].first()
    fe = feat[feat["time_ist"] == ENTRY].set_index("day")
    days = available_intraday_days(store)
    exp_days = [d for d in days if dte_by.get(d, 9) <= 1.0]
    exp_set = pd.to_datetime(exp_days)

    # map each day -> its next expiry (cycle id); pick one entry per cycle = highest-DTE day
    day_dt = pd.to_datetime(days)
    cyc = {}
    for d, dd in zip(days, day_dt):
        later = exp_set[exp_set >= dd]
        if len(later):
            cyc.setdefault(str(later[0].date()), []).append((d, float(dte_by.get(d, np.nan))))

    # --- straddle per-cycle trades (entry=max dte, hold to expiry) ------------------------------
    trades = []
    for E, members in cyc.items():
        if E not in [str(pd.to_datetime(x).date()) for x in exp_days]:
            pass
        entry_day, dte = max(members, key=lambda m: (m[1] if m[1] == m[1] else -1))
        if not (dte >= 1.5) or entry_day not in fe.index:
            continue
        sp_exp = close_spot.get(E if E in close_spot.index else None)
        if sp_exp is None:
            # E is a date-string; find matching day key
            cand = [d for d in exp_days if str(pd.to_datetime(d).date()) == E]
            if not cand:
                continue
            sp_exp = close_spot.get(cand[0])
        if sp_exp is None or not np.isfinite(sp_exp):
            continue
        df = load_intraday_day(store, entry_day)
        if df is None:
            continue
        en = _straddle(df, ENTRY)
        if en is None or en[1] <= 0:
            continue
        K, prem0, spot0 = en
        pnl_prem = (prem0 - abs(float(sp_exp) - K)) / prem0 * 100.0 - STRAD_COST
        r_cap = pnl_prem / 100.0 * (prem0 / spot0) / MARGIN_RATE      # return on posted margin
        e = fe.loc[entry_day]
        trades.append({"date": E, "entry": entry_day, "r": r_cap,
                       "iv": float(e["atm_iv"]) if e["atm_iv"] == e["atm_iv"] else np.nan,
                       "vanna": abs(float(e["vanna"]))})
    tr = pd.DataFrame(trades).sort_values("date").reset_index(drop=True)
    iv_hi = tr["iv"].quantile(2 / 3)
    van_hi = tr["vanna"].quantile(2 / 3)

    def _equity(mask):
        eq, dates, cur = [], [], 10000.0
        for _, t in tr.iterrows():
            if mask(t):
                cur *= (1 + F_OPT * t["r"])
            eq.append(round(cur, 2)); dates.append(t["date"])
        return dates, eq

    d1, s1 = _equity(lambda t: True)
    d2, s2 = _equity(lambda t: t["iv"] >= iv_hi)
    d3, s3 = _equity(lambda t: t["vanna"] >= van_hi)

    # --- S4 ORB daily + S5 buy-hold (daily grid) ----------------------------------------------
    orb_d, orb_e, cur = [], [], 10000.0
    bh_d, bh_e = [], []
    sp0 = None
    for day in days:
        s = load_intraday_day(store, day)
        if s is None:
            continue
        sp = s.groupby("time_ist")["spot"].first()
        mins = np.array([int(t[:2]) * 60 + int(t[3:]) for t in sp.index]); val = sp.to_numpy()
        o = np.argsort(mins); mins, val = mins[o], val[o]
        if (val.max() - val.min()) / val[0] * 100 > 12:
            continue
        r = _orb_daytrade(mins, val)
        if r is not None:
            cur *= (1 + r / 100.0)
            orb_d.append(day); orb_e.append(round(cur, 2))
        if sp0 is None:
            sp0 = float(val[0])
        bh_d.append(day); bh_e.append(round(10000.0 * float(val[-1]) / sp0, 2))

    curves = {
        "S1 Short straddle (every cycle)": {"dates": d1, "equity": s1},
        "S2 Short straddle (high IV only)": {"dates": d2, "equity": s2},
        "S3 Short straddle (SIDEWAYS regime)": {"dates": d3, "equity": s3},
        "S4 ORB breakout (directional)": {"dates": orb_d, "equity": orb_e},
        "S5 Buy & hold Nifty": {"dates": bh_d, "equity": bh_e},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"store": store, "curves": curves}), encoding="utf-8")

    _print(f"Strategy backtest · {store} · start Rs 10,000\n")
    _print(f"  {'strategy':<38} {'final Rs':>12} {'CAGR%':>7} {'maxDD%':>7} {'trades':>7}")
    n_by = {"S1 Short straddle (every cycle)": len(tr),
            "S2 Short straddle (high IV only)": int((tr['iv'] >= iv_hi).sum()),
            "S3 Short straddle (SIDEWAYS regime)": int((tr['vanna'] >= van_hi).sum()),
            "S4 ORB breakout (directional)": len(orb_e),
            "S5 Buy & hold Nifty": len(bh_e)}
    for name, c in curves.items():
        if not c["equity"]:
            continue
        fin, cagr, dd = _curve_stats(c["dates"], c["equity"])
        _print(f"  {name:<38} {fin:>12,.0f} {cagr:>6.1f}% {dd:>6.1f}% {n_by[name]:>7}")
    _print(f"\n  curves → {OUT}")
    _print("  ⚠️ gross of real frictions/slippage/liquidity; short-vol curves carry fat left-tail "
           "risk (see maxDD). Sizing: straddles f=0.5 on margin@12%, cost 2%/premium; ORB unlevered.")


if __name__ == "__main__":
    main()
