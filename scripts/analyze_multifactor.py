"""Phase B (offline) — does combining GEX + PCR + VIX beat GEX alone at predicting
the next-hour return? Weights are fit by OLS and scored OUT-OF-SAMPLE with
leave-one-day-out CV (the only honest metric at this sample size).

  python scripts/analyze_multifactor.py                 # NIFTY, 60-min horizon
  python scripts/analyze_multifactor.py --symbol BANKNIFTY --horizon 30

Factors (all point-in-time, value known at time t):
  gex     = magnet_gap = (magnet - spot)/spot   -- SIGN-FREE (magnet location is
            dealer-sign-invariant; the strongest single signal, IC ~+0.45 morning).
  pcr     = pcr_oi at t                          -- sign-free (put/call OI).
  vix     = india_vix at t                       -- implied vol level (sign-free).
  vix_chg = india_vix % change at t.

WHY leave-one-day-out: intraday rows within a day are correlated, so a random
split leaks. We hold out WHOLE days: fit weights on the other days, predict the
held-out day, pool the held-out predictions, then IC = Spearman(pooled OOS
prediction, forward return). We also print the IN-SAMPLE IC so the overfit gap is
visible. Factors are z-scored using TRAIN-fold stats only (no test leakage).

NOT a predictor, NOT a verdict. n is tiny and war-confounded — read the OOS number
with the caveats, and expect combos to *look* good in-sample and often NOT survive
out of sample.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from scripts.analyze_ic import (MORNING_CUTOFF_MIN, _ist_minute, _parse_utc,  # noqa: E402
                                forward_index)
from scripts.analyze_timeseries import load_all  # noqa: E402

FACTORS_ALL = ["gex", "pcr", "vix", "vix_chg"]


def build_mf_day(df: pd.DataFrame, horizon_min: float) -> pd.DataFrame:
    """One day's point-in-time (factors_t, forward-return) rows."""
    d = df.copy()
    d["utc"] = d["timestamp_utc"].apply(_parse_utc)
    d = d[d["utc"].notna()].reset_index(drop=True)
    if len(d) < 2:
        return pd.DataFrame()

    def col(name):
        return (pd.to_numeric(d[name], errors="coerce").to_numpy(float)
                if name in d.columns else np.full(len(d), np.nan))

    spot = d["spot"].to_numpy(float)
    mag, pcr, vix, vixchg = col("magnet"), col("pcr_oi"), col("india_vix"), col("vix_pct_chg")
    times = d["utc"].tolist()
    out = []
    for i in range(len(d)):
        j = forward_index(times, i, horizon_min)
        if j is None:
            continue
        out.append({
            "day": d["date_ist"].iloc[i] if "date_ist" in d.columns else "?",
            "min_ist": _ist_minute(times[i]),
            "gex": (mag[i] - spot[i]) / spot[i],
            "pcr": pcr[i],
            "vix": vix[i],
            "vix_chg": vixchg[i],
            "fwd": float(np.log(spot[j] / spot[i])),
        })
    return pd.DataFrame(out)


def build_mf_samples(days: dict[str, pd.DataFrame], horizon_min: float) -> pd.DataFrame:
    frames = [build_mf_day(df, horizon_min) for df in days.values()]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _zfit(train: np.ndarray):
    mu = train.mean(axis=0)
    sd = train.std(axis=0)
    sd[sd == 0] = 1.0
    return mu, sd


def _ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """OLS with intercept via least squares -> [b0, b1, ...]."""
    A = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    return beta


def lodo_ic(samples: pd.DataFrame, factors: list[str]):
    """Leave-one-DAY-out OOS IC of an OLS combo of ``factors``. Returns a dict with
    the out-of-sample IC (the honest number), the in-sample IC (to expose overfit),
    and the full-sample standardized weights (for interpretation)."""
    from scipy.stats import spearmanr

    s = samples.dropna(subset=factors + ["fwd", "day"]).reset_index(drop=True)
    days = list(pd.unique(s["day"]))
    if len(days) < 3 or len(s) < 12:
        return None
    X = s[factors].to_numpy(float)
    y = s["fwd"].to_numpy(float)
    dayarr = s["day"].to_numpy()

    preds = np.full(len(s), np.nan)
    for hd in days:
        te = dayarr == hd
        tr = ~te
        if tr.sum() < len(factors) + 2 or te.sum() == 0:
            continue
        mu, sd = _zfit(X[tr])                    # standardize on TRAIN only
        beta = _ols((X[tr] - mu) / sd, y[tr])
        preds[te] = beta[0] + ((X[te] - mu) / sd) @ beta[1:]

    m = np.isfinite(preds)
    if m.sum() < 5:
        return None
    oos_ic, oos_p = spearmanr(preds[m], y[m])

    mu, sd = _zfit(X)                            # full-sample fit (in-sample + weights)
    beta = _ols((X - mu) / sd, y)
    ins = beta[0] + ((X - mu) / sd) @ beta[1:]
    ins_ic, _ = spearmanr(ins, y)
    return {
        "oos_ic": float(oos_ic), "oos_p": float(oos_p), "n": int(m.sum()),
        "in_sample_ic": float(ins_ic), "weights": dict(zip(factors, beta[1:])),
        "days": len(days),
    }


def _fmt(r):
    if r is None:
        return "n/a (too few days/rows)"
    # weights are bps of forward return per +1 sigma of the (z-scored) factor.
    w = "  ".join(f"{k}={v * 1e4:+.1f}" for k, v in r["weights"].items())
    gap = r["in_sample_ic"] - r["oos_ic"]
    return (f"OOS IC={r['oos_ic']:+.3f} (p={r['oos_p']:.3f}, n={r['n']})   "
            f"in-sample={r['in_sample_ic']:+.3f}  [overfit gap {gap:+.3f}]   "
            f"w(bps/σ)[{w}]")


def analyze(symbol=None, horizon_min: float = 60.0) -> str:
    idx = config.get_index(symbol)
    days = load_all(symbol)
    L: list[str] = []
    add = L.append
    add("=" * 82)
    add(f"MULTI-FACTOR IC — {idx.label} ({idx.symbol}) — GEX + PCR + VIX vs "
        f"forward {horizon_min:g}-min return")
    add("=" * 82)
    samples = build_mf_samples(days, horizon_min)
    if samples.empty or len(samples) < 12:
        add(f"Too few point-in-time samples ({len(samples)}). Capture more, then re-run.")
        return "\n".join(L)

    add(f"\nSamples {len(samples)} over {samples['day'].nunique()} days.  "
        "OOS = leave-one-day-out (honest); in-sample shown to expose overfit.")
    add("gex=magnet_gap (sign-free), pcr=pcr_oi, vix=india_vix, vix_chg=VIX %chg. "
        "All point-in-time.")

    combos = [["gex"], ["pcr"], ["vix"], ["gex", "pcr"], ["gex", "vix"],
              ["gex", "pcr", "vix"], ["gex", "pcr", "vix", "vix_chg"]]
    sessions = [("ALL", samples),
                ("MORNING (<12:00)", samples[samples["min_ist"] < MORNING_CUTOFF_MIN]),
                ("AFTERNOON (>=12:00)", samples[samples["min_ist"] >= MORNING_CUTOFF_MIN])]

    for label, sub in sessions:
        add("\n" + "-" * 82)
        add(f"{label}   (n_rows={len(sub)})")
        base = lodo_ic(sub, ["gex"])
        base_oos = base["oos_ic"] if base else None
        # Only a POSITIVE gex-alone baseline is a real signal to beat; if it's <=0
        # there is no OOS signal here and "beating" it is meaningless (overfit noise).
        base_has_signal = base_oos is not None and base_oos > 0.02
        for factors in combos:
            r = lodo_ic(sub, factors)
            tag = ""
            if r and factors != ["gex"]:
                if not base_has_signal:
                    tag = "   (gex-alone has no OOS signal here — comparison moot)"
                else:
                    delta = r["oos_ic"] - base_oos
                    tag = f"   vs gex-alone: {delta:+.3f}" + (
                        " ✓ better" if delta > 0.02 else
                        " ✗ worse" if delta < -0.02 else " ~flat")
            add(f"  {'+'.join(factors):22}  {_fmt(r)}{tag}")

    add("\n" + "=" * 82)
    add("READ: compare each combo's OOS IC to gex-alone (same row's `vs gex-alone`). "
        "A combo only 'helps' if its OOS IC beats gex-alone — the in-sample column "
        "will almost always look better and is NOT evidence. VIX is a volatility "
        "(magnitude) read, so it rarely adds to a DIRECTIONAL-return IC. n is tiny "
        "and war-confounded: treat any gain as a hint to re-test, not an edge.")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-factor (GEX+PCR+VIX) OOS IC search.")
    ap.add_argument("--symbol", default=config.DEFAULT_INDEX,
                    help=f"index ({'/'.join(config.INDICES)}); default {config.DEFAULT_INDEX}")
    ap.add_argument("--horizon", type=float, default=60.0, help="forward horizon (min)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(analyze(symbol=args.symbol, horizon_min=args.horizon))


if __name__ == "__main__":
    main()
