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


def _intraday_record(df, entry, close, wing=5, stop_mult=1.5):
    """Everything the sell-IV variants need from one day: ATM straddle entry/exit, OTM-wing (iron-fly)
    values, and the intraday straddle path for the stop. Points (not %). None if unusable."""
    e = df[df["time_ist"] == entry]; c = df[df["time_ist"] == close]
    if e.empty or c.empty:
        return None
    spot0 = float(e["spot"].iloc[0])
    ce = e[e["side"] == "CE"]
    if ce.empty:
        return None
    K = float(ce["strike"].iloc[(ce["strike"] - spot0).abs().argmin()])
    atm_off = int(ce[ce["strike"] == K]["off"].iloc[0]) if "off" in e.columns and \
        not ce[ce["strike"] == K].empty else 0

    def px(dd, strike, side):
        r = dd[(dd["strike"] == strike) & (dd["side"] == side)]["close"]
        return float(r.iloc[0]) if len(r) and r.iloc[0] >= 0 else None

    c0, p0 = px(e, K, "CE"), px(e, K, "PE")
    exs, exp_ = px(c, K, "CE"), px(c, K, "PE")
    if None in (c0, p0, exs, exp_):
        return None
    prem0 = c0 + p0
    naked = prem0 - (exs + exp_)                         # short straddle P&L (points)
    rec = {"spot0": spot0, "prem0": prem0, "naked": naked, "fly": None, "maxloss": None, "stop": naked}
    # iron fly: buy wings at ATM ±`wing` offsets
    wcs = e[(e["side"] == "CE") & (e.get("off") == atm_off + wing)]["strike"]
    wps = e[(e["side"] == "PE") & (e.get("off") == atm_off - wing)]["strike"]
    if len(wcs) and len(wps):
        wcs, wps = float(wcs.iloc[0]), float(wps.iloc[0])
        w0c, w0p, wxc, wxp = px(e, wcs, "CE"), px(e, wps, "PE"), px(c, wcs, "CE"), px(c, wps, "PE")
        if None not in (w0c, w0p, wxc, wxp):
            rec["fly"] = naked + ((wxc + wxp) - (w0c + w0p))     # short straddle + long wings
            rec["maxloss"] = max(min(wcs - K, K - wps) - (prem0 - (w0c + w0p)), 1e-6)
    # stop: first stamp (entry→close) where straddle mark ≥ stop_mult × entry premium
    ka = df[df["strike"] == K]
    cea = ka[ka["side"] == "CE"][["time_ist", "close"]].rename(columns={"close": "c"})
    pea = ka[ka["side"] == "PE"][["time_ist", "close"]].rename(columns={"close": "p"})
    path = cea.merge(pea, on="time_ist")
    if not path.empty:
        path["m"] = path["time_ist"].map(lambda t: int(t[:2]) * 60 + int(t[3:]))
        em = int(entry[:2]) * 60 + int(entry[3:])
        path = path[path["m"] >= em].sort_values("m")
        val = path["c"] + path["p"]
        hit = val[val >= stop_mult * prem0]
        stop_exit = float(hit.iloc[0]) if len(hit) else (exs + exp_)
        rec["stop"] = prem0 - stop_exit
    return rec


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

    # --- S4 ORB + S5 buy-hold + S6-S10 intraday sell-IV variants (daily grid) ------------------
    orb_d, orb_e, cur = [], [], 10000.0
    bh_d, bh_e = [], []
    rec = []                    # per-day intraday records (naked/fly/stop @09:20, naked @11:00, IV)
    sp0 = None
    for day in days:
        s = load_intraday_day(store, day)
        if s is None:
            continue
        sp = s.groupby("time_ist")["spot"].first()
        times = sp.index.to_numpy()
        mins = np.array([int(t[:2]) * 60 + int(t[3:]) for t in times]); val = sp.to_numpy()
        o = np.argsort(mins); mins, val, times = mins[o], val[o], times[o]
        if (val.max() - val.min()) / val[0] * 100 > 12:
            continue
        r = _orb_daytrade(mins, val)
        if r is not None:
            cur *= (1 + r / 100.0)
            orb_d.append(day); orb_e.append(round(cur, 2))
        if sp0 is None:
            sp0 = float(val[0])
        bh_d.append(day); bh_e.append(round(10000.0 * float(val[-1]) / sp0, 2))
        if day not in fe.index or fe.loc[day, "atm_iv"] != fe.loc[day, "atm_iv"]:
            continue
        close_stamp = str(times[-1])
        r9 = _intraday_record(s, ENTRY, close_stamp)
        r11 = _intraday_record(s, "11:00", close_stamp)
        if r9 is None:
            continue
        rec.append({"day": day, "iv": float(fe.loc[day, "atm_iv"]),
                    "spot0": r9["spot0"], "prem0": r9["prem0"], "naked": r9["naked"],
                    "fly": r9["fly"], "maxloss": r9["maxloss"], "stop": r9["stop"],
                    "n11": (r11["naked"] if r11 else None), "sp11": (r11["spot0"] if r11 else None),
                    "pr11": (r11["prem0"] if r11 else None)})
    R = pd.DataFrame(rec).sort_values("day").reset_index(drop=True)

    iv_hi = R["iv"].quantile(2 / 3)                                  # full-sample tercile (S6-S8)
    # trailing IV-rank (point-in-time, fixes S6's look-ahead) for S9/S10
    ivv = R["iv"].to_numpy(); rank = np.full(len(R), np.nan)
    for i in range(len(R)):
        if i >= 20:
            w = ivv[max(0, i - 252):i]
            rank[i] = (w <= ivv[i]).mean()
    R["rank"] = rank
    ratio = (R["pr11"] / R["sp11"]).replace([np.inf, -np.inf], np.nan)
    med_ratio = ratio.median()

    def equity(mask, pts_col, spot_col, cost_legs=1.0, size=None):
        d, e, c = [], [], 10000.0
        for i, t in R.iterrows():
            take = mask(t) and t[pts_col] == t[pts_col] and t[spot_col] == t[spot_col]
            if take:
                cost = STRAD_COST / 100.0 * t["prem0"] * cost_legs
                r = (t[pts_col] - cost) / (t[spot_col] * MARGIN_RATE)
                f = F_OPT if size is None else size(t)
                c *= (1 + f * r)
            d.append(t["day"]); e.append(round(c, 2))
        return d, e

    d6, s6 = equity(lambda t: t["iv"] >= iv_hi, "naked", "spot0")
    d7, s7 = equity(lambda t: t["iv"] >= iv_hi and t["fly"] == t["fly"], "fly", "spot0", 2.0)
    d8, s8 = equity(lambda t: t["iv"] >= iv_hi, "stop", "spot0")
    d9, s9 = equity(lambda t: t["rank"] >= 0.66, "naked", "spot0")

    def vt_size(t):
        rr = (t["pr11"] / t["sp11"]) if t["sp11"] == t["sp11"] else med_ratio
        return float(np.clip(F_OPT * med_ratio / rr, 0.2, 1.0))
    d10, s10 = equity(lambda t: t["rank"] >= 0.66 and t["n11"] == t["n11"], "n11", "sp11",
                      1.0, vt_size)
    d11, s11 = equity(lambda t: t["rank"] >= 0.66, "stop", "spot0")   # leak-free IV-rank + stop

    curves = {
        "S1 Short straddle (every cycle)": {"dates": d1, "equity": s1},
        "S2 Short straddle (high IV only)": {"dates": d2, "equity": s2},
        "S3 Short straddle (SIDEWAYS regime)": {"dates": d3, "equity": s3},
        "S4 ORB breakout (directional)": {"dates": orb_d, "equity": orb_e},
        "S5 Buy & hold Nifty": {"dates": bh_d, "equity": bh_e},
        "S6 Short straddle (high IV, intraday 1-day)": {"dates": d6, "equity": s6},
        "S7 Iron fly (defined-risk, high IV)": {"dates": d7, "equity": s7},
        "S8 Short straddle + intraday stop": {"dates": d8, "equity": s8},
        "S9 Short straddle (IV-rank, leak-free)": {"dates": d9, "equity": s9},
        "S10 Late entry + vol-target size": {"dates": d10, "equity": s10},
        "S11 Best deployable (IV-rank + stop)": {"dates": d11, "equity": s11},
    }
    it = R; iv_hi6 = iv_hi                                            # for the summary counts below
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"store": store, "curves": curves}), encoding="utf-8")

    _print(f"Strategy backtest · {store} · start Rs 10,000\n")
    _print(f"  {'strategy':<38} {'final Rs':>12} {'CAGR%':>7} {'maxDD%':>7} {'trades':>7}")
    n_by = {"S1 Short straddle (every cycle)": len(tr),
            "S2 Short straddle (high IV only)": int((tr['iv'] >= iv_hi).sum()),
            "S3 Short straddle (SIDEWAYS regime)": int((tr['vanna'] >= van_hi).sum()),
            "S4 ORB breakout (directional)": len(orb_e),
            "S5 Buy & hold Nifty": len(bh_e),
            "S6 Short straddle (high IV, intraday 1-day)": int((R['iv'] >= iv_hi).sum()),
            "S7 Iron fly (defined-risk, high IV)": int(((R['iv'] >= iv_hi) & R['fly'].notna()).sum()),
            "S8 Short straddle + intraday stop": int((R['iv'] >= iv_hi).sum()),
            "S9 Short straddle (IV-rank, leak-free)": int((R['rank'] >= 0.66).sum()),
            "S10 Late entry + vol-target size": int(((R['rank'] >= 0.66) & R['n11'].notna()).sum()),
            "S11 Best deployable (IV-rank + stop)": int((R['rank'] >= 0.66).sum())}
    for name, c in curves.items():
        if not c["equity"]:
            continue
        fin, cagr, dd = _curve_stats(c["dates"], c["equity"])
        _print(f"  {name:<44} {fin:>11,.0f} {cagr:>6.1f}% {dd:>6.1f}% {n_by.get(name,0):>7}")
    _print(f"\n  curves → {OUT}")
    _print("  ⚠️ gross of real frictions/slippage/liquidity; short-vol curves carry fat left-tail "
           "risk (see maxDD). Sizing: straddles f=0.5 on margin@12%, cost 2%/premium; ORB unlevered.")


if __name__ == "__main__":
    main()
