"""Study #22 — does VOLUME conviction sharpen the futures price+OI BUILD-UP direction, or is
high volume EXHAUSTION (reversal)?

The dashboard tags each day's index-futures as long build-up / short build-up / short covering /
long unwinding from the price×OI matrix (`app/futures_buildup.py::classify_futures_buildup`), with
VOLUME carried only as an intensity side-gauge, never fed into the label. The open question: does
combining volume as *conviction* improve the directional read? Prior turnover work (findings §4a) found
high-turnover OPTIONS build-ups tend to REVERSE (exhaustion), low-turnover quietly continue — the
opposite of "high volume = trust it more". Untested for FUTURES build-up specifically. We test it here.

Daily raw F&O bhavcopy (instr IDF, 543 days, ~2 yr). Per day, point-in-time at the close:
  dprice   = front-contract close − prev_close        (price move sign)
  agg_doi  = Σ ΔOI across serial months               (rollover-netted OI build)
  turnover = agg_vol / agg_oi                          (conviction, unit-consistent)
  rvol     = agg_vol / trailing-20d median agg_vol     (relative volume, point-in-time)
Target = next-day spot return (t→t+1), day-detrended (strip index drift, the S4-ORB lesson).

  python scripts/analyze_futures_buildup_volume.py            # NIFTY   [symbol]

⚠️ EXPLORATORY. Build-up direction is a signed bet (NOT the GEX dealer-sign landmine — this is the
unambiguous futures price+OI matrix). Overlapping horizons mild; single index. Not a trade rule."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bhavcopy_raw import available_days, load_raw_fo_day          # noqa: E402
from app.futures_buildup import classify_futures_buildup, buildup_bias  # noqa: E402

# directional score per label: strong ±1, weak (covering/unwinding) ±0.5
_SCORE = {"long build-up": 1.0, "short build-up": -1.0,
          "short covering": 0.5, "long unwinding": -0.5, "flat": 0.0}


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _day_row(day: str, symbol: str) -> dict | None:
    df = load_raw_fo_day(day, symbol)
    if df is None:
        return None
    idf = df[df["instr"] == "IDF"].copy()
    if idf.empty or idf["expiry"].isna().all():
        return None
    for c in ("close", "prev_close", "oi", "doi", "volume", "spot"):
        idf[c] = pd.to_numeric(idf[c], errors="coerce")
    front = idf.loc[idf["expiry"].idxmin()]                       # near-month = min expiry
    dprice = float(front["close"] - front["prev_close"]) if front["prev_close"] == front["prev_close"] else np.nan
    agg_doi = float(idf["doi"].sum())
    agg_oi = float(idf["oi"].sum())
    agg_vol = float(idf["volume"].sum())
    spot = float(front["spot"]) if front["spot"] == front["spot"] else float(front["close"])
    if not np.isfinite(dprice) or agg_oi <= 0:
        return None
    return {"day": day, "dprice": dprice, "agg_doi": agg_doi, "agg_oi": agg_oi,
            "agg_vol": agg_vol, "spot": spot,
            "turnover": agg_vol / agg_oi}


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    days = available_days(symbol)
    rows = [r for r in (_day_row(d, symbol) for d in days) if r]
    d = pd.DataFrame(rows).reset_index(drop=True)
    if len(d) < 120:
        _print(f"Only {len(d)} usable days — run scripts/backfill_bhavcopy_raw.py first."); return

    d["label"] = [classify_futures_buildup(p, o) for p, o in zip(d["dprice"], d["agg_doi"])]
    d["score"] = d["label"].map(_SCORE).fillna(0.0)
    d["rvol"] = d["agg_vol"] / d["agg_vol"].rolling(20, min_periods=10).median()   # point-in-time
    d["turnover"] = d["turnover"]
    d["doi_norm"] = d["agg_doi"] / d["agg_oi"]                    # signed OI-build intensity
    # momentum / reversal factors (point-in-time: return INTO day t, known at close t)
    sp = d["spot"].to_numpy()
    d["mom1"] = d["spot"] / d["spot"].shift(1) - 1.0             # yesterday's return
    d["mom5"] = d["spot"] / d["spot"].shift(5) - 1.0             # trailing 5-day return
    # next-day spot return, day-detrended
    fwd = np.full(len(d), np.nan)
    for i in range(len(d) - 1):
        fwd[i] = (sp[i + 1] / sp[i] - 1.0) * 100.0
    d["fwd1"] = fwd - np.nanmedian(fwd)

    _print(f"Futures build-up × VOLUME conviction → next-day return  ·  {symbol}  ·  {len(d)} days "
           f"({d['day'].iloc[0]} … {d['day'].iloc[-1]})\n")

    # 1) baseline: does the build-up DIRECTION predict next day at all?
    ic = d[["score", "fwd1"]].dropna()
    r = ic["score"].corr(ic["fwd1"], method="spearman")
    _print(f"  Baseline  Spearman(buildup score, next-day ret) = {r:+.3f}   (n {len(ic)})")
    _print("  Mean next-day return by build-up label:")
    for lab in ["long build-up", "short covering", "long unwinding", "short build-up"]:
        sub = d[d["label"] == lab]["fwd1"].dropna()
        if len(sub) >= 15:
            _print(f"    {lab:<16} {sub.mean():+.3f}%  (bias {buildup_bias(lab):<22} n {len(sub)})")

    # 2) the key test — within each label, HIGH vs LOW conviction (turnover): continue or reverse?
    _print("\n  CONVICTION split — next-day return by (build-up × turnover tercile):")
    _print(f"    {'label':<16} {'LOW-vol':>9} {'HIGH-vol':>9} {'HIGH−LOW':>9}   {'n':>5}")
    for lab in ["long build-up", "short build-up", "short covering", "long unwinding"]:
        sub = d[d["label"] == lab].dropna(subset=["fwd1", "turnover"])
        if len(sub) < 30:
            continue
        q1, q2 = sub["turnover"].quantile([1 / 3, 2 / 3])
        lo, hi = sub[sub["turnover"] <= q1]["fwd1"], sub[sub["turnover"] >= q2]["fwd1"]
        _print(f"    {lab:<16} {lo.mean():>+8.3f}% {hi.mean():>+8.3f}% {hi.mean()-lo.mean():>+8.3f}%   {len(sub):>5}")

    # 3) does a conviction-WEIGHTED score beat the plain score? (IC comparison + OOS)
    #    weight the directional score by relative-volume rank (conviction) two ways:
    z = (d["rvol"] - d["rvol"].median())
    d["score_w"] = d["score"] * d["rvol"].clip(upper=3.0)         # confirm hypothesis: vol amplifies
    d["score_x"] = d["score"] / d["rvol"].clip(lower=0.3)         # exhaustion hypothesis: vol fades
    base = d[["score", "fwd1"]].dropna()
    _print("\n  Combined directional read — Spearman IC vs next-day return:")
    for name, col in [("plain score (price+OI only)", "score"),
                      ("× volume  (conviction amplifies)", "score_w"),
                      ("÷ volume  (exhaustion fades)", "score_x")]:
        s = d[[col, "fwd1"]].dropna()
        r = s[col].corr(s["fwd1"], method="spearman")
        _print(f"    {name:<34} IC {r:+.3f}   (n {len(s)})")

    # 4) walk-forward OOS: trade the build-up sign, gated on high vs low conviction
    cut = d["day"].iloc[int(len(d) * 0.70)]
    te = d[d["day"] >= cut].dropna(subset=["fwd1", "turnover", "score"])
    thr = d[d["day"] < cut]["turnover"].median()
    strong = te[(te["score"].abs() >= 1.0)]                       # only strong build/short-build labels
    hi_c = strong[strong["turnover"] >= thr]
    lo_c = strong[strong["turnover"] < thr]
    _print(f"\n  Walk-forward OOS (test ≥{cut}) — signed build-up P&L (long if score>0), by conviction:")
    for name, sub in [("ALL strong build-ups", strong),
                      ("HIGH-conviction only", hi_c), ("LOW-conviction only", lo_c)]:
        if len(sub) >= 15:
            pnl = np.sign(sub["score"]) * sub["fwd1"]
            _print(f"    {name:<24} mean {pnl.mean():+.3f}%  win {100*(pnl>0).mean():.0f}%  (n {len(sub)})")
    _print("\n  Combining HELPS only if a weighted IC clearly beats the plain score AND the OOS "
           "conviction-gated P&L separates. If HIGH−LOW is negative (high-vol reverses), volume is "
           "EXHAUSTION not conviction — and must NOT be wired as a naive confirm. Sign-free of GEX.")

    # 5) MULTI-FACTOR: does adding momentum / reversal / turnover to the buildup improve OOS?
    #    Leave-one-DAY-out CV (the honest metric): fit OLS on all-but-one day (z-scored on the
    #    train fold only), predict the held-out day, pool predictions → OOS IC. In-sample IC
    #    shown so the overfit gap is visible; a REAL fit has in-sample >= OOS.
    def _loo(cols):
        s = d[cols + ["fwd1"]].dropna().reset_index(drop=True)
        if len(s) < 80:
            return None
        X = s[cols].to_numpy(float); y = s["fwd1"].to_numpy(float)
        preds = np.full(len(s), np.nan)
        for i in range(len(s)):
            tr = np.arange(len(s)) != i
            mu, sd = X[tr].mean(0), X[tr].std(0); sd[sd == 0] = 1.0
            Xz = (X - mu) / sd
            A = np.column_stack([np.ones(tr.sum()), Xz[tr]])
            beta = np.linalg.lstsq(A, y[tr], rcond=None)[0]
            preds[i] = beta[0] + Xz[i] @ beta[1:]
        oos = pd.Series(preds).corr(pd.Series(y), method="spearman")
        mu, sd = X.mean(0), X.std(0); sd[sd == 0] = 1.0
        Xz = (X - mu) / sd
        beta = np.linalg.lstsq(np.column_stack([np.ones(len(s)), Xz]), y, rcond=None)[0]
        ins = pd.Series(beta[0] + Xz @ beta[1:]).corr(pd.Series(y), method="spearman")
        return {"oos": float(oos), "ins": float(ins), "n": len(s)}

    _print("\n  MULTI-FACTOR leave-one-day-out OOS — does adding a factor beat buildup-alone?")
    _print(f"    {'factors':<34} {'OOS IC':>7} {'in-sample':>10} {'overfit gap':>12}  {'n':>5}")
    base = _loo(["score"])
    base_has_signal = base is not None and base["oos"] > 0.02      # only a +ve base is worth beating
    for cols in [["score"], ["mom1"], ["mom5"], ["score", "mom1"], ["score", "mom5"],
                 ["score", "turnover"], ["score", "doi_norm"],
                 ["score", "mom1", "mom5", "turnover", "doi_norm"]]:
        r = _loo(cols)
        if r is None:
            continue
        tag = ""
        if cols != ["score"]:
            if not base_has_signal:
                tag = "  (base has no OOS signal — moot)"
            else:
                dlt = r["oos"] - base["oos"]
                tag = f"  vs base {dlt:+.3f}" + (" ✓" if dlt > 0.02 else " ✗" if dlt < -0.02 else " ~")
        _print(f"    {'+'.join(cols):<34} {r['oos']:>+7.3f} {r['ins']:>+10.3f} "
               f"{r['ins']-r['oos']:>+12.3f}  {r['n']:>5}{tag}")
    _print("\n  A factor HELPS only if (a) buildup-alone itself has a +ve OOS IC to beat, and (b) the "
           "combo's OOS IC clearly exceeds it with in-sample >= OOS (a NEGATIVE overfit gap = OOS>ins "
           "is a fluke, not a fit). Read OOS only; the in-sample column always flatters. Single daily "
           "index; multi-testing inflates in-sample. Sign-free of the GEX landmine.")


if __name__ == "__main__":
    main()
