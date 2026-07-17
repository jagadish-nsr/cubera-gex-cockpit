"""Phase B (offline) — Information Coefficient (IC) of GEX signals vs forward
returns, split by time-of-day (the morning-vs-afternoon question).

  python scripts/analyze_ic.py                  # 1-hour horizon, all days
  python scripts/analyze_ic.py --horizon 30      # 30-min forward horizon
  python scripts/analyze_ic.py --days 8          # only the last N captured days

IC = Spearman rank correlation between a signal at time t and the forward
log-return over the next ``horizon`` minutes (POINT-IN-TIME correct: the signal
uses only data at/<=t, the return uses only data >t). We report IC for the whole
session, for MORNING (<12:00 IST) vs AFTERNOON (>=12:00), per entry-hour, and a
DAY-BLOCK bootstrap CI (resampling whole days, since intraday rows are clustered).

Primary signal: MAGNET GAP = (magnet - spot)/spot. The magnet (peak |net_gex|
strike) is INVARIANT to the dealer-sign convention, so this IC is SIGN-FREE — it
does not depend on the unvalidated India landmine (CLAUDE.md). The pin hypothesis
predicts IC > 0 (price pulled toward the magnet).

Anchor BENCHMARK: we also compute the IC of two trivial reversion anchors — the
day OPEN and a running-mean (VWAP-proxy) — so we can see whether the GEX magnet
beats "intraday reversion to some anchor" or merely re-expresses it.

Reuses the cleaned captures from scripts/analyze_timeseries.load_all. Pure-function
core (hand-checkable); the CLI just prints. NOT a predictor, NOT a verdict.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from scripts.analyze_timeseries import load_all  # noqa: E402

MORNING_CUTOFF_MIN = 12 * 60      # IST minute that splits morning / afternoon
MATCH_TOL_MIN = 8.0               # how close a row must be to t+horizon to pair
BOOTSTRAP_N = 2000
# Signals to score. magnet_gap/flip_gap/open_gap/vwap_gap are sign-free (the
# magnet & flip locations and the price anchors don't depend on the dealer sign);
# net_gex is sign-DEPENDENT (its IC sign flips under the inverted convention).
SIGNALS = ["magnet_gap", "flip_gap", "net_gex", "open_gap", "vwap_gap"]


def _parse_utc(ts: str):
    try:
        return datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _ist_minute(utc: datetime) -> int:
    ist = utc + timedelta(hours=5, minutes=30)
    return ist.hour * 60 + ist.minute


def forward_index(times: list, i: int, horizon_min: float, tol_min: float = MATCH_TOL_MIN):
    """Index of the row nearest to times[i] + horizon, within tol; else None.
    Pure + hand-checkable: the point-in-time forward-return matcher."""
    target = times[i] + timedelta(minutes=horizon_min)
    best, best_d = None, float("inf")
    for j in range(i + 1, len(times)):
        d = abs((times[j] - target).total_seconds()) / 60.0
        if d < best_d:
            best_d, best = d, j
        if times[j] > target:
            break
    return best if (best is not None and best_d <= tol_min) else None


def build_day_samples(df: pd.DataFrame, horizon_min: float) -> pd.DataFrame:
    """One day's (signal_t, forward-return) rows with all signals + anchors.

    Anchors: open_gap uses the day's FIRST spot; vwap_gap uses the expanding mean
    of spot up to and including t (a running-mean reversion anchor — a VWAP proxy
    without index bar-volume). All gaps are (anchor - spot)/spot, sized so a
    reversion signal predicts the forward return with POSITIVE IC.
    """
    d = df.copy()
    d["utc"] = d["timestamp_utc"].apply(_parse_utc)
    d = d[d["utc"].notna()].reset_index(drop=True)
    if len(d) < 2:
        return pd.DataFrame()
    spot = d["spot"].to_numpy(float)
    mag = pd.to_numeric(d["magnet"], errors="coerce").to_numpy(float)
    flip = pd.to_numeric(d["flip"], errors="coerce").to_numpy(float)
    ng = pd.to_numeric(d["net_gex"], errors="coerce").to_numpy(float)
    day_open = spot[0]
    run_mean = np.array([spot[: i + 1].mean() for i in range(len(spot))])  # expanding mean
    times = d["utc"].tolist()

    out = []
    for i in range(len(d)):
        j = forward_index(times, i, horizon_min)
        if j is None:
            continue
        out.append({
            "day": d["date_ist"].iloc[i] if "date_ist" in d.columns else "?",
            "min_ist": _ist_minute(times[i]),
            "hour": _ist_minute(times[i]) // 60,
            "magnet_gap": (mag[i] - spot[i]) / spot[i],
            "flip_gap": (flip[i] - spot[i]) / spot[i] if np.isfinite(flip[i]) else np.nan,
            "net_gex": ng[i],
            "open_gap": (day_open - spot[i]) / spot[i],
            "vwap_gap": (run_mean[i] - spot[i]) / spot[i],
            "fwd": float(np.log(spot[j] / spot[i])),
        })
    return pd.DataFrame(out)


def build_samples(days: dict[str, pd.DataFrame], horizon_min: float) -> pd.DataFrame:
    frames = [build_day_samples(df, horizon_min) for df in days.values()]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


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


def session_split(samples: pd.DataFrame):
    """(morning, afternoon) sub-frames by MORNING_CUTOFF_MIN."""
    return (samples[samples["min_ist"] < MORNING_CUTOFF_MIN],
            samples[samples["min_ist"] >= MORNING_CUTOFF_MIN])


def bootstrap_ic_ci(samples: pd.DataFrame, signal: str, n_boot: int = BOOTSTRAP_N,
                    seed: int = 0):
    """Day-block bootstrap 95% CI for IC: resample WHOLE days (rows within a day
    are correlated, so the honest unit of resampling is the day). Returns
    (lo, median, hi) or NaNs if too few days."""
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


def analyze(symbol: str | None = None, last_n: int | None = None,
            horizon_min: float = 60.0) -> str:
    idx = config.get_index(symbol)
    days = load_all(symbol, last_n)
    L: list[str] = []
    add = L.append
    add("=" * 78)
    add(f"INFORMATION COEFFICIENT — {idx.label} ({idx.symbol}) — GEX signals vs "
        f"forward {horizon_min:g}-min return")
    add("=" * 78)
    samples = build_samples(days, horizon_min)
    if samples.empty or len(samples) < 10:
        add(f"Too few point-in-time samples ({len(samples)}). Capture more, then re-run.")
        return "\n".join(L)

    add(f"\nPoint-in-time samples (signal_t, fwd {horizon_min:g}m): {len(samples)}  "
        f"over {samples['day'].nunique()} days")
    add("IC = Spearman rank corr(signal_t, forward return). * = p<0.05.")
    add("Sign-free signals (dealer-sign-invariant): magnet_gap, flip_gap, open_gap, "
        "vwap_gap.  net_gex is sign-DEPENDENT.")

    morning, afternoon = session_split(samples)
    for sig in SIGNALS:
        r_all, p_all, n_all = spearman_ic(samples, sig)
        r_m, p_m, n_m = spearman_ic(morning, sig)
        r_a, p_a, n_a = spearman_ic(afternoon, sig)
        add("\n" + "-" * 78)
        add(f"SIGNAL = {sig}"
            + ("   [pin hypothesis: IC>0]" if sig == "magnet_gap" else ""))
        add(f"  ALL         IC={_f(r_all)} {_star(p_all)} p={_f(p_all)} n={n_all}")
        add(f"  MORNING <12 IC={_f(r_m)} {_star(p_m)} p={_f(p_m)} n={n_m}")
        add(f"  AFTERNOON   IC={_f(r_a)} {_star(p_a)} p={_f(p_a)} n={n_a}")
        add("  by entry hour (IST):")
        for h in sorted(samples["hour"].unique()):
            rb, pb, nb = spearman_ic(samples[samples["hour"] == h], sig)
            se = 1 / np.sqrt(nb) if nb and nb > 1 else float("nan")
            add(f"    {h:02d}:00  IC={_f(rb)} {_star(pb)} p={_f(pb)} n={nb:3}  (~SE {_f(se,2)})")

    # Day-block bootstrap CI for the primary signal, morning vs afternoon.
    add("\n" + "=" * 78)
    add("DAY-BLOCK BOOTSTRAP 95% CI — magnet_gap (resamples whole days)")
    for label, sub in (("MORNING", morning), ("AFTERNOON", afternoon)):
        lo, med, hi = bootstrap_ic_ci(sub, "magnet_gap")
        excl = "excludes 0" if np.isfinite(lo) and (lo > 0 or hi < 0) else "includes 0"
        add(f"  {label:9} IC 95% CI [{_f(lo)}, {_f(hi)}]  median {_f(med)}  -> {excl}")

    # Anchor benchmark: does the GEX magnet beat trivial reversion anchors?
    add("\n" + "=" * 78)
    add("ANCHOR BENCHMARK — morning IC: does the GEX magnet beat trivial anchors?")
    add("(all are reversion-to-anchor signals; higher morning IC = more informative)")
    bench = [(s, spearman_ic(morning, s)[0]) for s in ("magnet_gap", "vwap_gap", "open_gap")]
    bench.sort(key=lambda kv: (-(kv[1]) if np.isfinite(kv[1]) else 9e9))
    for name, r in bench:
        add(f"  {name:11} morning IC = {_f(r)}")
    best = bench[0][0]
    add(f"  -> highest morning IC: {best}"
        + ("  (GEX magnet beats the trivial anchors — it carries information)"
           if best == "magnet_gap" else
           "  (a TRIVIAL anchor matches/beats the magnet — magnet may just re-express "
           "intraday reversion; treat the magnet result with caution)"))

    add("\n" + "=" * 78)
    add("READ: this is exploratory. Small n, 15-min cadence (coarse for a "
        f"{horizon_min:g}m horizon), war-confounded, and the late afternoon has "
        "forward-truncated samples. Suggestive of a MORNING-concentrated, sign-free "
        "magnet pull — not an established edge. Re-run as more clean days accrue.")
    return "\n".join(L)


def _f(x, nd=3):
    return "  nan" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:+.{nd}f}"


def _star(p):
    return "*" if (p is not None and np.isfinite(p) and p < 0.05) else " "


def main() -> None:
    ap = argparse.ArgumentParser(description="IC of GEX signals vs forward returns, by time-of-day.")
    ap.add_argument("--horizon", type=float, default=60.0, help="forward horizon in minutes")
    ap.add_argument("--days", type=int, default=None, help="only the last N captured days")
    ap.add_argument("--symbol", default=config.DEFAULT_INDEX,
                    help=f"index to analyse ({'/'.join(config.INDICES)}); default {config.DEFAULT_INDEX}")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(analyze(symbol=args.symbol, last_n=args.days, horizon_min=args.horizon))


if __name__ == "__main__":
    main()
