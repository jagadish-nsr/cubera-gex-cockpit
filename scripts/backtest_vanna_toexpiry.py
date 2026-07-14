"""T4b remainder — is the vanna-VRP residual TRADEABLE when the straddle is held TO EXPIRY?

Study #11 found: at matched IV+DTE, higher |vanna| → larger variance-risk-premium (partial +0.11 NIFTY
/ +0.24 BankNifty). Study #11b showed it does NOT monetize on an entry→CLOSE (same-day) straddle —
intraday theta/tail noise swamps it. The ONE untested horizon is the proper **hold-to-EXPIRY** short
straddle, which captures the full premium the VRP is about (not a 1-day slice).

Clean trick: a straddle held to expiry has a DETERMINISTIC payoff |spot_expiry − K|, so we only need the
ENTRY premium and the SPOT AT EXPIRY — not the strike's price on intermediate days (which the ATM±10
store can't track anyway):
    seller P&L% = (entry_prem − |spot_expiry − K|) / entry_prem × 100 − cost
Entry = ATM straddle at 09:20 on a non-expiry day (nearest weekly, expiry_code=1). Expiry spot = the
close spot on the next expiry session (dte≈0). Sign-free.

  python scripts/backtest_vanna_toexpiry.py            # NIFTY   [store]  [cost%]

A real edge must show: partial(|vanna|, seller P&L | IV+DTE) clearly > 0, HIGH-vanna P&L > LOW across
DTE bands, AND a walk-forward OOS selected P&L that beats unconditional net of cost. ⚠️ overlapping
holds autocorrelate; rough cost; low prior (11b was null intraday)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from straddle_gex_timing import _straddle   # noqa: E402  (sibling; K-aware ATM straddle)

ENTRY = "09:20"


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _partial(x, y, ctrls):
    import numpy as np, pandas as pd
    d = pd.DataFrame({"x": x, "y": y})
    for i, c in enumerate(ctrls):
        d[f"z{i}"] = c
    d = d.dropna()
    if len(d) < 40:
        return float("nan")
    r = d.rank()
    A = np.c_[np.ones(len(d)), *[r[f"z{i}"] for i in range(len(ctrls))]]
    ex = r["x"] - A @ np.linalg.lstsq(A, r["x"], rcond=None)[0]
    ey = r["y"] - A @ np.linalg.lstsq(A, r["y"], rcond=None)[0]
    return float(pd.Series(ex).corr(pd.Series(ey), method="spearman"))


def main() -> None:
    import numpy as np, pandas as pd
    from data.dhan import load_intraday_day, available_intraday_days
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    cost = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    feat = load_features(store)
    if feat is None:
        _print("No feature cache.")
        return
    dte_by = feat.groupby("day")["dte"].first()
    close_spot = feat.sort_values("time_ist").groupby("day")["spot"].last()
    fe = feat[feat["time_ist"] == ENTRY].set_index("day")
    days = available_intraday_days(store)
    exp_days = sorted([d for d in days if dte_by.get(d, 9) <= 1.0])       # expiry sessions (dte≈0)
    exp_dt = pd.to_datetime(exp_days)

    rows = []
    for day in days:
        if day not in fe.index:
            continue
        dte = float(dte_by.get(day, np.nan))
        if not (dte >= 1.5):                    # need a real hold; skip on/near expiry entries
            continue
        # next expiry session on/after entry = the nearest-expiry straddle's settlement
        d0 = pd.to_datetime(day)
        later = exp_dt[exp_dt >= d0]
        if len(later) == 0:
            continue
        exp_day = exp_days[list(exp_dt).index(later[0])]
        if abs((later[0] - d0).days - dte) > 3:  # data gap → the "next expiry" isn't this straddle's
            continue
        sp_exp = close_spot.get(exp_day)
        if sp_exp is None or not np.isfinite(sp_exp):
            continue
        df = load_intraday_day(store, day)
        if df is None:
            continue
        en = _straddle(df, ENTRY)
        if en is None or en[1] <= 0:
            continue
        K, prem0, _ = en
        pnl = (prem0 - abs(float(sp_exp) - K)) / prem0 * 100.0 - cost
        e = fe.loc[day]
        rows.append({"day": day, "seller": pnl, "vanna": abs(float(e["vanna"])),
                     "iv": float(e["atm_iv"]) * 100 if e["atm_iv"] == e["atm_iv"] else np.nan,
                     "dte": dte})
    df = pd.DataFrame(rows)
    if len(df) < 150:
        _print(f"Only {len(df)} usable holds.")
        return
    df["dband"] = pd.cut(df["dte"], [1.4, 3, 5, 99], labels=["1.5–3d", "3–5d", ">5d"])

    _print(f"Vanna → hold-to-EXPIRY short straddle · {store} · {len(df)} holds · sell ATM {ENTRY} → "
           f"expiry settle · cost {cost:.1f}%\n")
    _print(f"  UNCONDITIONAL sell-every-day: mean net {df['seller'].mean():+.2f}%  "
           f"median {df['seller'].median():+.2f}%  win {100*(df['seller']>0).mean():.0f}%\n")

    ic = df["vanna"].corr(df["seller"], method="spearman")
    p_iv = _partial(df["vanna"], df["seller"], [df["iv"]])
    p_ivd = _partial(df["vanna"], df["seller"], [df["iv"], df["dte"]])
    _print(f"  Spearman(|vanna|, seller P&L)   = {ic:+.3f}")
    _print(f"  partial | IV                    = {p_iv:+.3f}")
    _print(f"  partial | IV + DTE              = {p_ivd:+.3f}   ← edge only if clearly > 0\n")

    _print("By DTE band — seller net P&L, LOW vs HIGH |vanna| tercile (IV shown for the confound):")
    _print(f"  {'DTE band':<8} {'LOW-van':>9} {'HIGH-van':>9} {'HIGH−LOW':>9}  {'IV lo→hi':>12}  {'n':>5}")
    for b in ["1.5–3d", "3–5d", ">5d"]:
        sub = df[df["dband"] == b]
        if len(sub) < 30:
            continue
        q1, q2 = sub["vanna"].quantile([1 / 3, 2 / 3])
        lo, hi = sub[sub["vanna"] <= q1], sub[sub["vanna"] >= q2]
        _print(f"  {b:<8} {lo['seller'].mean():>+8.2f}% {hi['seller'].mean():>+8.2f}% "
               f"{hi['seller'].mean()-lo['seller'].mean():>+8.2f}%  "
               f"{lo['iv'].mean():>5.1f}→{hi['iv'].mean():<5.1f}  {len(sub):>5}")

    # walk-forward OOS: sell only when |vanna| > its (DTE×IV)-cell train median
    days_s = sorted(df["day"])
    cut = days_s[int(len(days_s) * 0.70)]
    tr, te = df[df["day"] < cut].copy(), df[df["day"] >= cut].copy()
    tr["ivb"] = pd.qcut(tr["iv"], 3, labels=False, duplicates="drop")
    edges = tr["iv"].quantile([1 / 3, 2 / 3]).tolist()
    te["ivb"] = np.digitize(te["iv"], edges)
    thr = tr.groupby(["dband", "ivb"], observed=True)["vanna"].median()
    sel = te[te.apply(lambda r: r["vanna"] >= thr.get((r["dband"], r["ivb"]), np.inf), axis=1)]
    _print(f"\nWalk-forward OOS (train <{cut}, test ≥): SELL only when |vanna| > its (DTE×IV)-cell "
           f"train-median:")
    _print(f"  selected {len(sel)}/{len(te)} test holds · net {sel['seller'].mean():+.2f}% "
           f"(win {100*(sel['seller']>0).mean():.0f}%)  vs unconditional {te['seller'].mean():+.2f}%")
    _print("\n  Tradeable only if partial|IV+DTE > 0, HIGH−LOW positive across bands, AND OOS selected "
           "beats unconditional. Else even the to-expiry hold doesn't monetize the #11 residual.")


if __name__ == "__main__":
    main()
