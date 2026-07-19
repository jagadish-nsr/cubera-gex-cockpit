"""Study #25 PRE-REGISTERED follow-up (ONE run, kill bar fixed in docs #25).

Hypothesis: the Market-Profile vol-over-pricing ratio r = realized-acceptance ÷ implied-move
(<1 ⇒ options priced more move than price accepted) predicts the FORWARD short-straddle P&L — the
vol-pricing target, NOT spot-RV (the venue where #11's +0.11 residual died in #11b/#18).

Strictly point-in-time: r(t) is known at the CLOSE of day t; it predicts the ATM short-straddle
seller return on day t+1 (enter at t+1 open, exit t+1 close). Predictor = r.shift(1).

SURVIVES only if ALL of:
  (1) partial(r, straddle_ret | existing sell-IV signal = IV at entry) is non-zero,
  (2) the sign is stable across IV terciles AND DTE terciles,
  (3) it clears walk-forward OOS (day-block 70/30) on the actual straddle P&L,
  (4) it is not contemporaneous-VRP leakage — partial against yesterday's realized/implied must
      not collapse it (r ≈ realized÷implied ≈ the VRP itself).
Else: footnote in #25, not a #26. Prior: LOW.

  python scripts/backtest_mp_straddle.py            # NIFTY
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TRADING_DAYS = 252.0


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _spearman(x, y):
    import numpy as np
    from scipy.stats import spearmanr
    m = np.isfinite(x) & np.isfinite(y)
    return spearmanr(x[m], y[m]).correlation if m.sum() > 10 else float("nan")


def _partial(x, y, *ctrls):
    import numpy as np
    from scipy.stats import rankdata
    m = np.isfinite(x) & np.isfinite(y)
    for c in ctrls:
        m &= np.isfinite(c)
    if m.sum() < 30:
        return float("nan")
    R = [rankdata(v[m]) for v in (x, y, *ctrls)]
    Z = np.column_stack([np.ones(m.sum())] + [R[i] for i in range(2, len(R))])

    def resid(v):
        beta, *_ = np.linalg.lstsq(Z, v, rcond=None)
        return v - Z @ beta
    return float(np.corrcoef(resid(R[0]), resid(R[1]))[0, 1])


def _straddle_returns(store: str):
    """Per-day ATM short-straddle SELLER return (open→close, intraday): (open_prem − close_prem) /
    open_prem, from the ATM (off==0) CE+PE close premiums in the 1-min store. Positive = the seller
    kept premium (realized < what decayed). Also the entry DTE proxy via the day's option coverage."""
    import numpy as np
    import pandas as pd
    from data.dhan import available_intraday_days, load_intraday_day
    rows = []
    for d in available_intraday_days(store):
        df = load_intraday_day(store, d)
        if df is None or getattr(df, "empty", True) or "off" not in df.columns:
            continue
        atm = df[df["off"] == 0]
        if atm.empty:
            continue
        stamps = sorted(atm["time_ist"].unique())
        if len(stamps) < 30:
            continue
        t0, t1 = stamps[0], stamps[-1]

        def _strad(t):
            g = atm[atm["time_ist"] == t]
            c = g[g["side"] == "CE"]["close"]
            p = g[g["side"] == "PE"]["close"]
            if c.empty or p.empty:
                return None
            cv, pv = float(c.iloc[0]), float(p.iloc[0])
            return (cv + pv) if (cv == cv and pv == pv) else None
        s0, s1 = _strad(t0), _strad(t1)
        if not s0 or s0 <= 0 or s1 is None:
            continue
        rows.append({"day": d, "straddle_ret": (s0 - s1) / s0})     # seller return (fraction of premium)
    return pd.DataFrame(rows).set_index("day")


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.market_profile import profiles_frame
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    _print(f"Study #25 follow-up · MP vol-over-pricing r → forward short-straddle P&L · {store}\n")

    mp = profiles_frame(store)
    feat = load_features(store)
    if mp.empty or feat is None:
        _print("No data."); return
    feat = feat.sort_values(["day", "time_ist"])
    openf = feat.groupby("day").first()[["spot", "atm_iv", "dte"]]
    df = mp.join(openf).dropna(subset=["spot", "atm_iv"])
    df["accept_pct"] = (df["va_width"] / 2.0) / df["open"] * 100.0
    df["implied_move_pct"] = df["atm_iv"] * np.sqrt(1.0 / TRADING_DAYS) * 100.0
    df["r"] = df["accept_pct"] / df["implied_move_pct"]          # known at CLOSE of day t

    sr = _straddle_returns(store)
    df = df.join(sr, how="inner").sort_index()
    # predictor is YESTERDAY's r; target is TODAY's straddle seller return (point-in-time)
    df["r_prev"] = df["r"].shift(1)
    df["ivrp_prev"] = (df["accept_pct"] / df["atm_iv"]).shift(1)   # ~ realized/implied of t-1 (VRP leak check)
    df["iv_entry"] = df["atm_iv"]                                  # existing sell-IV signal at entry
    df["dte_entry"] = df["dte"]
    df = df.dropna(subset=["r_prev", "straddle_ret", "iv_entry"])
    n = len(df)
    _print(f"days: {n}  ({df.index[0]} … {df.index[-1]})   "
           f"median straddle seller ret {df.straddle_ret.median()*100:+.1f}% of premium\n")

    A = {k: df[k].to_numpy() for k in
         ("straddle_ret", "r_prev", "iv_entry", "dte_entry", "ivrp_prev")}
    ic = _spearman(A["r_prev"], A["straddle_ret"])
    p_iv = _partial(A["r_prev"], A["straddle_ret"], A["iv_entry"])                 # (1) sell-IV partial
    p_ivdte = _partial(A["r_prev"], A["straddle_ret"], A["iv_entry"], A["dte_entry"])
    p_leak = _partial(A["r_prev"], A["straddle_ret"], A["ivrp_prev"])              # (4) VRP-leak partial
    _print("── r(t-1) → straddle seller ret(t) ────────────────────────────────────────────")
    _print(f"  raw IC {ic:+.3f}   | IV-entry {p_iv:+.3f}   | IV+DTE {p_ivdte:+.3f}   | prior-VRP {p_leak:+.3f}")

    # (2) sign stability across IV and DTE terciles
    def _tercile_signs(ctrl_key):
        q1, q2 = np.nanpercentile(A[ctrl_key], [33, 66])
        out = []
        for lab, m in (("lo", A[ctrl_key] <= q1), ("mid", (A[ctrl_key] > q1) & (A[ctrl_key] < q2)),
                       ("hi", A[ctrl_key] >= q2)):
            out.append((lab, _spearman(A["r_prev"][m], A["straddle_ret"][m])))
        return out
    _print("  sign across IV terciles:  " + "  ".join(f"{l}:{v:+.3f}" for l, v in _tercile_signs("iv_entry")))
    _print("  sign across DTE terciles: " + "  ".join(f"{l}:{v:+.3f}" for l, v in _tercile_signs("dte_entry")))

    # (3) walk-forward OOS on straddle P&L
    cut = int(n * 0.7)
    _print(f"  OOS (day-block 70/30):    IS {_spearman(A['r_prev'][:cut], A['straddle_ret'][:cut]):+.3f}   "
           f"OOS {_spearman(A['r_prev'][cut:], A['straddle_ret'][cut:]):+.3f}")

    _print("\n── PRE-REGISTERED VERDICT (all four required) ─────────────────────────────────")
    _print(f"  (1) IV-partial non-zero .......... {p_iv:+.3f}")
    _print(f"  (2) sign stable across IV & DTE .. (see terciles above)")
    _print(f"  (4) survives prior-VRP partial ... {p_leak:+.3f}  (≈0 ⇒ it IS the VRP, restated)")
    _print("  → PASS only if (1)&(4) materially non-zero AND same sign, terciles consistent, OOS holds.")


if __name__ == "__main__":
    main()
