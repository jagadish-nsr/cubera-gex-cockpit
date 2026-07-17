"""Scenario A1 — MAGNET LOCK (expiry-day pin convergence).  [docs/scenario-catalog.md]

Hypothesis: on/near expiry, when dealer-gamma is CONCENTRATED (top-tercile |charm|) and spot sits a
meaningful distance from a gravity level, spot is pulled toward that level and CLOSES near it. Which
anchor pins best — the gamma-weighted MAGNET, the PEAK-OI strike, or MAX-PAIN?

Metric (terminal, point-in-time, ROBUST — no ratio-by-small-number):
    closer = 1 if |spot_15:15 − target| < |spot_t − target| else 0     → report the RATE (% pulled in)
    shrink = (|spot_t − target| − |spot_15:15 − target|) / exp_move    → σ√τ-normalized magnitude
Close is the 15:15 spot (before the last-15-min settlement thrash, ≈ when a pin/blast trade is placed).
Measured ONLY when the starting gap is meaningful (d0 ≥ MIN_GAP_SIG · exp_move) — if spot is already
AT the level there is nothing to converge (that "already-pinned" fraction is reported separately).
[The earlier ratio metric exploded because the ATM±10 gamma-magnet hugs spot; hence closer-rate.]

PLACEBO (the honest control): a MIRROR target at the same distance on the OPPOSITE side
(2·spot − target). Same starting gap, so a real pin must have `closer(target) > closer(mirror)` —
otherwise it's just mean-reversion toward spot, not a pull to the level.

  python scripts/analyze_magnet_lock.py            # NIFTY   [store]

⚠️ Exploratory. Pooled-stamp tables autocorrelate; the headline uses ONE stamp/day (13:45, the user's
decision time). Sign-free (geometry only). Requires the levels-enriched feature cache."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIN_PER_YEAR = 252 * 375          # NSE trading minutes/yr (6h15m × 60 × 252) for σ√τ scaling
CLOSE_STAMP = "15:15"
DECISION_STAMPS = ["11:15", "13:45", "14:45"]   # one-obs/day snapshots (13:45 = the user's example)
TARGETS = [("magnet", "magnet"), ("peak-OI", "peak_oi_strike"), ("max-pain", "max_pain")]
MIN_GAP_SIG = 0.25                # only measure convergence when the start gap ≥ 0.25 × expected move


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _mins(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _prep(store):
    import numpy as np
    from app.intraday_features import load_features
    f = load_features(store)
    if f is None or "magnet" not in f.columns:
        _print("No levels-enriched feature cache — rebuild it first (app.intraday_features.build "
               "after adding the level columns).")
        return None
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)
    close = f[f["time_ist"] == CLOSE_STAMP].set_index("day")["spot"]
    f = f[f["day"].isin(close.index)].copy()
    f["close_spot"] = f["day"].map(close)
    f = f[f["time_ist"].map(_mins) < _mins(CLOSE_STAMP)].copy()      # only stamps before the close
    tau = (_mins(CLOSE_STAMP) - f["time_ist"].map(_mins)) / MIN_PER_YEAR
    f["exp_move"] = f["spot"] * f["atm_iv"] * np.sqrt(tau.clip(lower=1e-9))   # expected move to close
    f["abs_charm"] = f["charm"].abs()

    def _anchor(target):
        d0 = (f["spot"] - target).abs()
        d1 = (f["close_spot"] - target).abs()
        valid = d0 >= MIN_GAP_SIG * f["exp_move"]                   # meaningful gap to close
        closer = np.where(valid, (d1 < d0).astype(float), np.nan)
        shrink = np.where(valid, (d0 - d1) / f["exp_move"], np.nan)
        return closer, shrink, valid

    for name, col in TARGETS:
        f[f"closer_{name}"], f[f"shrink_{name}"], _ = _anchor(f[col])
    # mirror placebo for each target (same gap, opposite side)
    for name, col in TARGETS:
        f[f"closer_mir_{name}"], _, _ = _anchor(2 * f["spot"] - f[col])
    f["gap_sig"] = (f["spot"] - f["magnet"]).abs() / f["exp_move"]  # (context only)
    # peak-OI gap in σ√τ for the pinned/gap slice
    f["gap_sig_peak"] = (f["spot"] - f["peak_oi_strike"]).abs() / f["exp_move"]

    dte = f["dte"]
    f["dte_bucket"] = np.where(dte <= 1.5, "≤1.5d (expiry)",
                        np.where(dte <= 3.5, "1.5–3.5d",
                        np.where(dte <= 6.5, "3.5–6.5d", ">6.5d")))
    return f


def _rate(s):
    """closer-rate as a % over non-NaN entries, with n."""
    v = s.dropna()
    return (100.0 * v.mean() if len(v) else float("nan")), len(v)


def main() -> None:
    import numpy as np
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    f = _prep(store)
    if f is None:
        return
    order = ["≤1.5d (expiry)", "1.5–3.5d", "3.5–6.5d", ">6.5d"]
    _print(f"A1 · MAGNET LOCK · {store} · {f['day'].nunique()} days (close = {CLOSE_STAMP} spot · "
           f"gap ≥ {MIN_GAP_SIG}×exp-move)\n")

    # ---- (1) pooled: closer-RATE to each anchor vs its mirror, by DTE bucket -------------------
    _print("(1) `closer`-rate % toward each anchor [vs its MIRROR placebo], by DTE bucket "
           "(pooled; >50% & > mirror = real pull):")
    _print(f"  {'DTE bucket':>16} | " + " ".join(f"{n:>16}" for n, _ in TARGETS))
    for b in order:
        sub = f[f["dte_bucket"] == b]
        if len(sub) < 200:
            continue
        cells = []
        for n, _ in TARGETS:
            r, k = _rate(sub[f"closer_{n}"])
            rm, _ = _rate(sub[f"closer_mir_{n}"])
            cells.append(f"{r:>5.1f} [{rm:>4.1f}]")
        _print(f"  {b:>16} | " + " ".join(f"{c:>16}" for c in cells))
    _print()

    # ---- (2) headline: expiry day, ONE stamp/day at each decision time -------------------------
    _print("(2) EXPIRY DAY (≤1.5d), one obs/day — closer-rate % [mirror] (median σ-shrink), n days:")
    _print(f"  {'@ stamp':>8} | " + " ".join(f"{n:>22}" for n, _ in TARGETS))
    exp = f[f["dte_bucket"] == "≤1.5d (expiry)"]
    for stp in DECISION_STAMPS:
        sub = exp[exp["time_ist"] == stp]
        if len(sub) < 20:
            _print(f"  {stp:>8} | (only {len(sub)} days)")
            continue
        cells = []
        for n, _ in TARGETS:
            r, k = _rate(sub[f"closer_{n}"])
            rm, _ = _rate(sub[f"closer_mir_{n}"])
            sh = sub[f"shrink_{n}"].median()
            cells.append(f"{r:>4.0f}[{rm:>4.0f}]({sh:>+4.2f})n{k:<3}")
        _print(f"  {stp:>8} | " + " ".join(f"{c:>22}" for c in cells))
    _print()

    # ---- (3) expiry @13:45: peak-OI closer-rate by |charm| tercile × starting-gap bucket -------
    _print("(3) EXPIRY @ 13:45 — closer-rate→PEAK-OI by |charm| tercile × σ√τ starting-gap "
           "(prediction: highest in HIGH-charm + a real (mid) gap):")
    sub = exp[exp["time_ist"] == "13:45"].dropna(subset=["closer_peak-OI", "abs_charm",
                                                          "gap_sig_peak"]).copy()
    if len(sub) < 45:
        _print(f"  Only {len(sub)} usable expiry days at 13:45 — too thin to slice; see (2).")
    else:
        cq = sub["abs_charm"].quantile([1 / 3, 2 / 3]).tolist()
        gq = sub["gap_sig_peak"].quantile([1 / 3, 2 / 3]).tolist()
        sub["cterc"] = np.digitize(sub["abs_charm"], cq)
        sub["gterc"] = np.digitize(sub["gap_sig_peak"], gq)
        _print(f"  {'':>12}" + "".join(f"{g:>13}" for g in ["small-gap", "mid-gap", "large-gap"]))
        for ci, cl in [(2, "HIGH-charm"), (1, "mid-charm"), (0, "low-charm")]:
            cells = []
            for gi in range(3):
                cell = sub[(sub["cterc"] == ci) & (sub["gterc"] == gi)]["closer_peak-OI"]
                cells.append(f"{100*cell.mean():>6.0f}%[{len(cell):>2}]" if len(cell) >= 5
                             else f"{'·':>13}")
            _print(f"  {cl:>12}" + "".join(f"{c:>13}" for c in cells))

    # ---- (4) how often is spot ALREADY pinned (peak-OI within 0.25 exp-move)? ------------------
    _print("\n(4) Already-pinned share — spot within 0.25×exp-move of PEAK-OI (no gap to close):")
    for b in order:
        sub = f[f["dte_bucket"] == b]
        if len(sub) < 200:
            continue
        pinned = 100.0 * (sub["gap_sig_peak"] < MIN_GAP_SIG).mean()
        _print(f"  {b:>16} : {pinned:>5.1f}% of stamps already pinned")

    _print("\n  Read: closer-rate >50% AND above its [mirror] = a genuine pull to the level. Expiry + "
           "HIGH-charm should be strongest; peak-OI/max-pain (discrete strikes) are the real targets "
           "(the ATM±10 gamma-magnet hugs spot). ⚠️ exploratory; sign-free; small-n cells noisy.")


if __name__ == "__main__":
    main()
