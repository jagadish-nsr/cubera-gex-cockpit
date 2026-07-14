"""How much does the market actually MOVE per day in each dashboard vanna bucket?

The live gauge labels the regime SIDEWAYS (HIGH |vanna| = calm) / neutral / TRENDY (LOW |vanna| =
wild) and reports an ANNUALIZED forward RV %, which isn't intuitive. This translates the buckets into
a concrete per-DAY move: the daily high-low range and the open→close move, in % AND in index points,
from the accurate raw 1-min spot.

Bucketing matches the gauge's tercile logic but at the DAY level: each day's representative |vanna| =
the MEDIAN across its 5-min stamps; terciles across days → HIGH (sideways) / MID / LOW (trendy). The
gauge switches to |charm| at ≤1.5 DTE, so expiry days are reported separately (bucketed by |charm|),
not mixed into the vanna buckets.

  daily_hl%  = (day high − day low) / day open × 100       (1-min spot)
  daily_oc%  = |close − open| / open × 100

  python scripts/analyze_vanna_daily_move.py            # NIFTY   [store]

⚠️ Descriptive (not a forecast test); 1-min-sampled range; sign-free."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _daily_moves(store):
    """Per-day HL range / open-close move (%, points) from raw 1-min spot."""
    import pandas as pd
    from data.dhan import load_intraday_day, available_intraday_days
    rows = []
    for d in available_intraday_days(store):
        df = load_intraday_day(store, d)
        if df is None or df.empty:
            continue
        sp = df.groupby("time_ist")["spot"].first().sort_index()
        if len(sp) < 60 or sp.iloc[0] <= 0:
            continue
        op, hi, lo, cl = sp.iloc[0], sp.max(), sp.min(), sp.iloc[-1]
        hl_pct = (hi - lo) / op * 100
        if hl_pct > 12:            # a >12% intraday range is a corrupt Dhan spot tick, not a real day
            continue
        rows.append({"day": d, "hl_pct": hl_pct, "hl_pts": hi - lo,
                     "oc_pct": abs(cl - op) / op * 100, "oc_pts": abs(cl - op), "open": op})
    return pd.DataFrame(rows)


def _mad(s):
    """Median absolute deviation (robust spread): median(|x − median(x)|)."""
    med = s.median()
    return (s - med).abs().median()


def _report(metric_col, sub, mv):
    """Print the per-bucket daily-move table (robust stats: median, MAD, IQR) for one metric, and
    return the SIDEWAYS→TRENDY separation ratio (LOW-bucket median HL ÷ HIGH-bucket median HL)."""
    d = sub.merge(mv, on="day").dropna(subset=[metric_col])
    q1, q2 = d[metric_col].quantile([1 / 3, 2 / 3])
    _print(f"  |{metric_col.upper()}| buckets  (n={len(d)} days · terciles of daily median |{metric_col}|)")
    _print(f"    {'bucket (dashboard)':>26} | {'daily HL range %  (median ± MAD  [IQR])':>40}  {'~pts':>6} | "
           f"{'open→close %  (med ± MAD)':>24}")
    hl_by = {}
    for label, key, m in [("HIGH → SIDEWAYS / calm", "hi", d[metric_col] >= q2),
                          ("MID → neutral", "mid", (d[metric_col] > q1) & (d[metric_col] < q2)),
                          ("LOW → TRENDY / wild", "lo", d[metric_col] <= q1)]:
        g = d[m]
        hl, oc = g["hl_pct"], g["oc_pct"]
        hl_by[key] = hl.median()
        cell = (f"{hl.median():.2f} ± {_mad(hl):.2f}  [{hl.quantile(.25):.2f}–{hl.quantile(.75):.2f}]")
        occ = f"{oc.median():.2f} ± {_mad(oc):.2f}"
        _print(f"    {label:>26} | {cell:>40}  {g['hl_pts'].median():>4.0f}pt | {occ:>24}")
    ratio = hl_by["lo"] / hl_by["hi"] if hl_by["hi"] else float("nan")
    _print(f"    → TRENDY÷SIDEWAYS separation on daily HL: {ratio:.2f}×")
    return ratio


def main() -> None:
    from app.intraday_features import load_features
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    f = load_features(store)
    if f is None:
        _print("No feature cache.")
        return
    mv = _daily_moves(store)
    # day-level regime metrics = median |metric| across the day's stamps; DTE from the cache
    day = f.groupby("day").agg(vanna=("vanna", lambda s: s.abs().median()),
                               charm=("charm", lambda s: s.abs().median()),
                               gex=("net_gex", lambda s: s.abs().median()),
                               dte=("dte", "first")).reset_index()
    non_exp = day[day["dte"] > 1.5]
    exp = day[day["dte"] <= 1.5]

    _print(f"Daily MOVE per regime bucket · {store} · {len(mv)} days "
           f"(compare |vanna| vs |charm| vs |GEX| — higher separation = better vol discriminator)\n")
    _print("NON-EXPIRY days (the gauge uses |vanna| here):")
    for mcol in ["vanna", "charm", "gex"]:
        _report(mcol, non_exp[["day", mcol]], mv)
        _print()
    _print("EXPIRY days ≤1.5 DTE (the gauge switches to |charm| here):")
    for mcol in ["charm", "gex", "vanna"]:
        _report(mcol, exp[["day", mcol]], mv)
        _print()
    _print("  Read: HIGH concentration (SIDEWAYS) days move LESS; LOW (TRENDY) move MORE. The "
           "separation × is the discriminator — the gauge picks the metric with the cleanest split "
           "(|vanna| off-expiry, |charm| at 0-DTE). Descriptive, sign-free, 1-min range.")


if __name__ == "__main__":
    main()
