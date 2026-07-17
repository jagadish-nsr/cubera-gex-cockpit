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
from scripts.analyze_timeseries import (MORNING_CUTOFF_MIN, _ist_minute, _parse_utc,  # noqa: E402
                                forward_index, load_all)

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


def _day_open(days: dict[str, pd.DataFrame], col: str, transform=None) -> dict[str, float]:
    """day -> that day's OPENING value of ``col`` (first in-session reading; PIT).
    ``transform`` maps the raw value (e.g. abs). Days lacking the column are omitted."""
    out: dict[str, float] = {}
    for name, df in days.items():
        if col not in df.columns:
            continue
        v = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(v):
            x = float(v.iloc[0])
            out[name] = transform(x) if transform else x
    return out


def day_open_vix(days):
    return _day_open(days, "india_vix")


def day_gamma_magnitude(days):
    """|net GEX| at the day's open — the SIGN-FREE size of net dealer gamma
    (invariant to flipping the dealer sign, since |−x|=|x|). Bigger => a stronger
    net pinning force (before we know its direction)."""
    return _day_open(days, "net_gex", abs)


def spearman_ic(df: pd.DataFrame, signal: str):
    """(IC, p_value, n) — Spearman rank corr of signal vs fwd, NaN-safe."""
    from scipy import stats
    if df.empty or signal not in df.columns:
        return (float("nan"), float("nan"), 0)
    x = df[signal].to_numpy(float)
    y = df["fwd"].to_numpy(float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 5 or np.unique(x[m]).size < 2:
        return (float("nan"), float("nan"), int(m.sum()))
    r, p = stats.spearmanr(x[m], y[m])
    return (float(r), float(p), int(m.sum()))


def bootstrap_ic_ci(samples: pd.DataFrame, signal: str, n_boot: int = 2000,
                    seed: int = 0):
    """Day-block bootstrap 95% CI for IC: resample WHOLE days."""
    groups = [g for _, g in samples.groupby("day") if len(g) >= 3]
    if len(groups) < 3:
        return (float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    vals = []
    k = len(groups)
    for _ in range(n_boot):
        pick = pd.concat([groups[i] for i in rng.integers(0, k, k)], ignore_index=True)
        r, _, _ = spearman_ic(pick, signal)
        if np.isfinite(r):
            vals.append(r)
    if not vals:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return (float(lo), float(np.median(vals)), float(hi))


def _conditioner_report(days, horizon_min, symbol, *, title, metric_name, day_metric,
                        low_label, high_label, hypothesis, threshold=None,
                        valfmt="{:.2f}") -> str:
    """Split whole days by a day-level metric (median, or a fixed ``threshold``) and
    compare the sign-free magnet_gap IC in the low vs high bucket, morning /
    afternoon / all, with day-block bootstrap CIs. A confound table prints the
    metric beside VIX, |net GEX| and the news flag so overlaps are visible."""
    from scripts.analyze_timeseries import news_day_flag

    samples = build_mf_samples(days, horizon_min)
    L: list[str] = []
    add = L.append
    add("=" * 82)
    add(title)
    add("=" * 82)
    if len(day_metric) < 4 or samples.empty:
        add(f"Too few days with {metric_name} (need >=4). Capture more, then re-run.")
        return "\n".join(L)

    if threshold is None:
        thr = float(np.median(list(day_metric.values())))
        split_desc = "MEDIAN"
    else:
        thr = float(threshold)
        split_desc = "FIXED"
    low_days = {d for d, v in day_metric.items() if v <= thr}
    high_days = {d for d, v in day_metric.items() if v > thr}
    add(f"\nWhole-day split on {metric_name}")
    add(f"  at the {split_desc} = {valfmt.format(thr)}  "
        f"({low_label}: {len(low_days)} days, {high_label}: {len(high_days)} days).")
    add("Signal = magnet_gap (sign-free GEX). IC = Spearman(magnet_gap_t, forward "
        f"{horizon_min:g}m return). Day-block bootstrap 95% CI. Exploratory — tiny n.")

    def block(name, sub):
        add("\n" + "-" * 82)
        add(name)
        for reg, dset in ((low_label, low_days), (high_label, high_days)):
            ss = sub[sub["day"].isin(dset)]
            ic, p, n = spearman_ic(ss, "gex")
            lo, med, hi = bootstrap_ic_ci(ss, "gex")
            ci = f"[{lo:+.2f}, {hi:+.2f}]" if np.isfinite(lo) else "[n/a]"
            excl = "excl 0" if np.isfinite(lo) and (lo > 0 or hi < 0) else "incl 0"
            add(f"  {reg:22} IC={ic:+.3f}  p={p:.3f}  n={n:3}  "
                f"days={ss['day'].nunique():2}  bootCI {ci} ({excl})")

    mor = samples[samples["min_ist"] < MORNING_CUTOFF_MIN]
    aft = samples[samples["min_ist"] >= MORNING_CUTOFF_MIN]
    block("MORNING (<12:00)", mor)
    block("AFTERNOON (>=12:00)", aft)
    block("ALL SESSION", samples)

    # Confound table: the split metric beside VIX, |net GEX|, and the news flag.
    vix, gam = day_open_vix(days), day_gamma_magnitude(days)
    add("\n" + "-" * 82)
    add("CONFOUND CHECK — split metric vs VIX / |net GEX| / news (same axis?)")
    add(f"  {'day':12} {'metric':>13} {'bin':>4}  {'openVIX':>7} {'|netGEX|open':>14}  news-flag")
    for d in sorted(day_metric):
        b = "HIGH" if day_metric[d] > thr else "low"
        add(f"  {d:12} {valfmt.format(day_metric[d]):>13} {b:>4}  "
            f"{vix.get(d, float('nan')):7.2f} {gam.get(d, float('nan')):14,.0f}  "
            f"{news_day_flag(days[d]) or '-'}")

    add("\n" + "=" * 82)
    add(hypothesis)
    return "\n".join(L)


def vix_regime_report(days, horizon_min=60.0, symbol=None, threshold=None) -> str:
    idx = config.get_index(symbol)
    return _conditioner_report(
        days, horizon_min, symbol,
        title=(f"VIX-REGIME CONDITIONER — {idx.label} ({idx.symbol}) — does the "
               "morning magnet-pull depend on the vol regime?"),
        metric_name="day-open India VIX", day_metric=day_open_vix(days),
        low_label="LOW-VIX (calm)", high_label="HIGH-VIX (stressed)",
        threshold=threshold, valfmt="{:.2f}",
        hypothesis=("READ: pin hypothesis -> LOW-VIX morning IC should EXCEED HIGH-VIX "
                    "(calm regimes pin). Mechanism: higher IV lowers gamma, weakening the "
                    "pin. Confound cols: if low-VIX days still carry news / high |netGEX|, "
                    "the gate isn't just the news quarantine. Caveats: tiny buckets, "
                    "compressed VIX range, high-VIX skews to early war-spike days, "
                    "median peeks — hypothesis-generating, not an edge."))


def gamma_regime_report(days, horizon_min=60.0, symbol=None, threshold=None) -> str:
    idx = config.get_index(symbol)
    return _conditioner_report(
        days, horizon_min, symbol,
        title=(f"GAMMA-MAGNITUDE CONDITIONER — {idx.label} ({idx.symbol}) — does the "
               "morning magnet-pull depend on how much dealer gamma is positioned?"),
        metric_name="day-open |net GEX| (sign-free dealer-gamma magnitude)",
        day_metric=day_gamma_magnitude(days),
        low_label="LOW-gamma (weak pin)", high_label="HIGH-gamma (strong pin)",
        threshold=threshold, valfmt="{:,.0f}",
        hypothesis=("READ: the DIRECT positioning test -> HIGH-gamma morning IC should "
                    "EXCEED LOW-gamma (more net dealer gamma => stronger pin => the magnet "
                    "pulls). |net GEX| is SIGN-FREE (invariant to the dealer-sign flip), so "
                    "this reads big-player positioning directly, not the news response. "
                    "Cross-check the VIX col: high gamma should coincide with LOW VIX (higher "
                    "IV mechanically lowers gamma) — if HIGH-gamma also wins on IC, it "
                    "corroborates the VIX gate via the actual mechanism. Caveats: |netGEX| "
                    "scale drifts with the OI build, tiny buckets, war-confounded, median peeks."))


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
    ap.add_argument("--conditioner", choices=["vix", "gamma"], default=None,
                    help="instead of the combo search, condition the magnet-pull IC on a "
                         "day-level regime: 'vix' (vol) or 'gamma' (|net GEX| positioning)")
    ap.add_argument("--vix-regime", action="store_true", help="alias for --conditioner vix")
    ap.add_argument("--threshold", type=float, default=None,
                    help="fixed split threshold for --conditioner (default: in-sample median)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    cond = args.conditioner or ("vix" if args.vix_regime else None)
    if cond == "vix":
        print(vix_regime_report(load_all(args.symbol), args.horizon, args.symbol, args.threshold))
    elif cond == "gamma":
        print(gamma_regime_report(load_all(args.symbol), args.horizon, args.symbol, args.threshold))
    else:
        print(analyze(symbol=args.symbol, horizon_min=args.horizon))


if __name__ == "__main__":
    main()
