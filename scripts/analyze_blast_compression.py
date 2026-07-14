"""Scenario H — GAMMA-BLAST COMPRESSION precondition (expiry days).  [docs/scenario-catalog.md]

Folklore / user hypothesis: a gamma blast needs a COILED morning — if spot's high-low range from the
open to 13:45 IST is small (< ~1%), the afternoon is primed to EXPAND (the blast). The honest null is
the opposite: intraday vol CLUSTERS, so a quiet morning usually predicts a quiet afternoon
(persistence, positive autocorrelation). This test discriminates coil (compression → expansion,
NEGATIVE morning→afternoon autocorr) from persistence (POSITIVE) — on EXPIRY DAYS specifically, where
morning pinning can suppress vol before an afternoon release.

Data: the RAW 1-minute Dhan store carries the underlying SPOT per minute (271 bars 09:15→13:45) — an
accurate high-low proxy (only sub-minute wicks are missed; negligible vs a 1% threshold). No OHLC
download needed.

  morning_range% = (max−min of 1-min spot, 09:15→13:45) / spot_open × 100
  aft_range%     = (max−min of 1-min spot, 13:45→close) / spot_13:45 × 100      ← blast expansion
  aft_move%      = |spot_close − spot_13:45| / spot_13:45 × 100                 ← directional magnitude
  expansion      = aft_range / morning_range

Point-in-time: morning_range is known AT 13:45 and predicts the after-13:45 outcome — a genuinely
tradeable decision time. EXPIRY = dte ≤ 1.5 (from the feature cache). Sign-free.

  python scripts/analyze_blast_compression.py            # NIFTY   [store]

⚠️ Exploratory; 1-min-sampled range (slight understatement); sign-free."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CUT = "13:45"


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _mins(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _build(store):
    import numpy as np
    import pandas as pd
    from data.dhan import load_intraday_day, available_intraday_days
    from app.intraday_features import load_features
    feat = load_features(store)
    dte_by_day = feat.groupby("day")["dte"].first() if feat is not None else None
    cut = _mins(CUT)
    rows = []
    for d in available_intraday_days(store):
        df = load_intraday_day(store, d)
        if df is None or df.empty:
            continue
        sp = df.groupby("time_ist")["spot"].first()
        pre = sp[[t for t in sp.index if _mins(t) <= cut]]
        post = sp[[t for t in sp.index if _mins(t) >= cut]]
        if len(pre) < 60 or len(post) < 6 or pre.iloc[0] <= 0:
            continue
        s1345 = post.iloc[0]
        rows.append({
            "day": d,
            "morning_range": (pre.max() - pre.min()) / pre.iloc[0] * 100,
            "aft_range": (post.max() - post.min()) / s1345 * 100,
            "aft_move": abs(post.iloc[-1] - s1345) / s1345 * 100,
            "dte": float(dte_by_day.get(d, np.nan)) if dte_by_day is not None else np.nan,
        })
    df = pd.DataFrame(rows)
    df["expansion"] = df["aft_range"] / df["morning_range"].clip(lower=1e-6)
    df["expiry"] = df["dte"] <= 1.5
    return df


def _tercile_table(sub, label):
    import numpy as np
    q1, q2 = sub["morning_range"].quantile([1 / 3, 2 / 3])
    _print(f"  {label} (morning-range terciles: low ≤{q1:.2f}% · high ≥{q2:.2f}%):")
    _print(f"    {'morning band':>14} | {'aft_range':>10} {'aft_move':>9} {'expansion':>10} "
           f"{'P(aft>morn)':>12}  {'n':>4}")
    for name, m in [("LOW (coiled)", sub["morning_range"] <= q1),
                    ("MID", (sub["morning_range"] > q1) & (sub["morning_range"] < q2)),
                    ("HIGH", sub["morning_range"] >= q2)]:
        g = sub[m]
        pexp = 100.0 * (g["aft_range"] > g["morning_range"]).mean()
        _print(f"    {name:>14} | {g['aft_range'].median():>9.2f}% {g['aft_move'].median():>8.2f}% "
               f"{g['expansion'].median():>10.2f} {pexp:>11.0f}%  {len(g):>4}")


def main() -> None:
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    df = _build(store)
    exp = df[df["expiry"]]
    non = df[~df["expiry"]]
    _print(f"H · BLAST COMPRESSION · {store} · {len(df)} days "
           f"({len(exp)} expiry, {len(non)} non-expiry)\n")

    _print(f"Morning HL-range distribution (09:15→{CUT}) on EXPIRY days — is the 1% cut discriminating?")
    q = exp["morning_range"].quantile([.1, .25, .5, .75, .9])
    _print("  pct:  10% {:.2f}   25% {:.2f}   50% {:.2f}   75% {:.2f}   90% {:.2f}   "
           "| share <1%: {:.0f}%\n".format(*q, 100 * (exp["morning_range"] < 1).mean()))

    _print("(1) EXPIRY DAYS — afternoon outcome by morning-range tercile "
           "(coil ⇒ LOW morning has the BIGGEST afternoon; persistence ⇒ the smallest):")
    _tercile_table(exp, "EXPIRY")
    _print()

    _print(f"(2) EXPIRY DAYS — the user's explicit cut (morning HL-range < 1% vs ≥ 1%):")
    _print(f"    {'morning':>12} | {'aft_range med':>13} {'aft_move med':>12} {'P(aft>1%)':>10}  {'n':>4}")
    for name, m in [("< 1% (tight)", exp["morning_range"] < 1.0),
                    ("≥ 1% (wide)", exp["morning_range"] >= 1.0)]:
        g = exp[m]
        if len(g) == 0:
            _print(f"    {name:>12} | (none)")
            continue
        pbig = 100.0 * (g["aft_range"] > 1.0).mean()
        _print(f"    {name:>12} | {g['aft_range'].median():>12.2f}% {g['aft_move'].median():>11.2f}% "
               f"{pbig:>9.0f}%  {len(g):>4}")
    _print()

    _print("(3) morning→afternoon autocorrelation (Spearman) — the discriminator "
           "(POSITIVE = persistence/quiet-begets-quiet; NEGATIVE = coil/compression→expansion):")
    for lbl, sub in [("EXPIRY", exp), ("non-expiry", non)]:
        r_rng = sub["morning_range"].corr(sub["aft_range"], method="spearman")
        r_mv = sub["morning_range"].corr(sub["aft_move"], method="spearman")
        _print(f"    {lbl:>11}: ρ(morning, aft_range) = {r_rng:+.3f}   "
               f"ρ(morning, aft_move) = {r_mv:+.3f}   (n={len(sub)})")

    _print("\n  Read: if EXPIRY ρ is NEGATIVE (and non-expiry isn't), tight mornings genuinely precede "
           "bigger afternoons — the coil is real and expiry-specific. If ρ is POSITIVE, it's ordinary "
           "vol persistence and the '<1% morning → blast' rule is folklore. ⚠️ sign-free; 1-min range.")


if __name__ == "__main__":
    main()
