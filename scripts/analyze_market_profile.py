"""Market Profile studies — the VOL-REGIME angle (the family that survives), NOT direction.

Priority = Test B: is the realized value-area width vs the option-IMPLIED expected move informative,
and — the killer — does it survive the IV partial (the control that killed |vanna|, which was a 1/σ
re-encoding of IV)? Test A: does today's profile SHAPE (VA-width%, mode count, time-at-POC) predict
TOMORROW's realized range incrementally over today's HL and today's |vanna| (Study #17 rig + a
feature)? Plus a direction sanity check against a same-distance MIRROR placebo (#12/#14/#16 rig).

Point-in-time: today's FULL profile is known at today's close, so predicting TOMORROW is clean.
1-min spot (NIFTY clean). Sign-free, descriptive. Decision-support, not a trade rule.

  python scripts/analyze_market_profile.py            # NIFTY
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
    """Rank-based partial correlation of x,y controlling for ctrls (residualise ranks, then corr)."""
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
    rx, ry = resid(R[0]), resid(R[1])
    return float(np.corrcoef(rx, ry)[0, 1])


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.market_profile import BIN_PTS, profiles_frame
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    _print(f"Market Profile · {store} · bin {BIN_PTS.get(store, 5.0):g} pt · 1-min TPO\n")

    mp = profiles_frame(store)
    if mp.empty:
        _print("No intraday days."); return
    feat = load_features(store)
    if feat is None:
        _print("No feature cache."); return

    # per-day features at the OPEN (earliest stamp) + day |vanna| (median abs, the regime metric)
    feat = feat.sort_values(["day", "time_ist"])
    openf = feat.groupby("day").first()[["spot", "atm_iv"]].rename(columns={"spot": "spot_open"})
    vanna = feat.groupby("day")["vanna"].apply(lambda s: s.abs().median()).rename("vanna_abs")
    df = mp.join(openf).join(vanna).dropna(subset=["spot_open", "atm_iv"])

    # today's realized range %, and the option-implied 1-day move % at the open (σ√τ, τ=1 trading day)
    df["hl_pct"] = (df["hi"] - df["lo"]) / df["open"] * 100.0
    df["implied_move_pct"] = df["atm_iv"] * np.sqrt(1.0 / TRADING_DAYS) * 100.0
    # realized ACCEPTANCE (value-area half-width, ~1σ) as a % of price
    df["accept_pct"] = (df["va_width"] / 2.0) / df["open"] * 100.0
    # the Test-B ratio: realized acceptance ÷ implied move. <1 ⇒ realized << implied ⇒ vol OVER-priced.
    df["r_accept_implied"] = df["accept_pct"] / df["implied_move_pct"]
    # tomorrow's realized range (the target); shift within the sorted day index
    df = df.sort_index()
    df["hl_next"] = df["hl_pct"].shift(-1)
    df = df.iloc[:-1]                                         # drop the last day (no tomorrow)
    n = len(df)
    _print(f"days: {n}  ({df.index[0]} … {df.index[-1]})")
    _print(f"median: today HL {df.hl_pct.median():.2f}% · implied move {df.implied_move_pct.median():.2f}% · "
           f"accept {df.accept_pct.median():.2f}% · r(accept/implied) {df.r_accept_implied.median():.2f}\n")

    A = {k: df[k].to_numpy() for k in
         ("hl_next", "hl_pct", "vanna_abs", "atm_iv", "r_accept_implied", "accept_pct",
          "va_width_pct", "tpoc_ratio", "n_modes")}

    _print("── TEST B · realized value-area vs implied move → TOMORROW's range ─────────────")
    _print(f"{'feature':<26}{'IC':>8}{'| today-HL':>12}{'| HL+|vanna|':>14}{'| ATM-IV':>10}")
    for name, key in [("r = accept ÷ implied", "r_accept_implied"),
                      ("accept_pct (VA half-w)", "accept_pct")]:
        ic = _spearman(A[key], A["hl_next"])
        p_hl = _partial(A[key], A["hl_next"], A["hl_pct"])
        p_hlv = _partial(A[key], A["hl_next"], A["hl_pct"], A["vanna_abs"])
        p_iv = _partial(A[key], A["hl_next"], A["atm_iv"])         # the killer partial
        _print(f"{name:<26}{ic:>+8.3f}{p_hl:>+12.3f}{p_hlv:>+14.3f}{p_iv:>+10.3f}")
    _print("  → survives if the ATM-IV partial stays materially non-zero (unlike |vanna|, a 1/σ "
           "re-encoding of IV). 'today-HL' / 'HL+|vanna|' = incremental over Study #17's controls.\n")

    _print("── TEST A · today's profile SHAPE → TOMORROW's range ──────────────────────────")
    _print(f"{'shape feature':<26}{'IC':>8}{'| today-HL':>12}{'| HL+|vanna|':>14}")
    for name, key in [("va_width_pct", "va_width_pct"), ("tpoc_ratio", "tpoc_ratio"),
                      ("n_modes", "n_modes")]:
        ic = _spearman(A[key], A["hl_next"])
        p_hl = _partial(A[key], A["hl_next"], A["hl_pct"])
        p_hlv = _partial(A[key], A["hl_next"], A["hl_pct"], A["vanna_abs"])
        _print(f"{name:<26}{ic:>+8.3f}{p_hl:>+12.3f}{p_hlv:>+14.3f}")
    _print("  → a real complement if it adds beyond today-HL AND |vanna|; a restatement if it "
           "collapses into today-HL.\n")

    # OOS: does Test-B's ratio hold in a held-out day block?
    cut = int(n * 0.7)
    ic_is = _spearman(A["r_accept_implied"][:cut], A["hl_next"][:cut])
    ic_oos = _spearman(A["r_accept_implied"][cut:], A["hl_next"][cut:])
    _print(f"── OOS (day-block 70/30) · r → tomorrow HL:  IS {ic_is:+.3f}  OOS {ic_oos:+.3f}\n")

    # DIRECTION sanity (low prior): does the prior-day POC beat a same-distance MIRROR as a
    # next-open attractor? (clean point-in-time: yesterday's POC vs today's open→close drift)
    df["poc_prev"] = df["poc"].shift(1)
    df["gap"] = df["open"] - df["close"].shift(1)            # overnight gap
    d2 = df.dropna(subset=["poc_prev"])
    toward = np.sign((d2["poc_prev"] - d2["open"])) * np.sign(d2["close"] - d2["open"])
    mirror = 2 * d2["open"] - d2["poc_prev"]                 # same-distance mirror level
    toward_mir = np.sign((mirror - d2["open"])) * np.sign(d2["close"] - d2["open"])
    _print("── DIRECTION sanity (low prior) · does price close TOWARD prior POC vs its MIRROR ──")
    _print(f"  toward prior POC: {(toward > 0).mean()*100:.0f}%   toward MIRROR: {(toward_mir > 0).mean()*100:.0f}%   "
           f"(≈50/50 and POC≈mirror ⇒ no attraction — expected)")


if __name__ == "__main__":
    main()
