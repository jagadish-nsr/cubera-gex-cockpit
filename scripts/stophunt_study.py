#!/usr/bin/env python
"""Run the stop-run / liquidity-sweep study (app.stophunt) over captured raw days and print a report.

Loads each day's raw tick archive (near-month) for a symbol, derives the prior-day high/low/close from
the preceding captured day, pools sweeps + placebo across days, and prints the overall / per-level-type /
per-signature breakdown. Descriptive; a placebo-gated tendency, never a trade call.

    python scripts/stophunt_study.py --symbol NIFTY
    python scripts/stophunt_study.py --symbol NIFTY --margin 4 --horizon 8 --days 2026-07-24,2026-07-27
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.big_money_live import available_raw_days, load_raw_ticks     # noqa: E402
from app.stophunt import Config, study                                # noqa: E402


def _prev_day_levels(prev_df):
    """Prior-day high/low/close from that day's near-month raw ticks."""
    if prev_df is None or getattr(prev_df, "empty", True) or "roll" not in prev_df.columns:
        return None
    n = prev_df[prev_df["roll"] == "near"]
    if n.empty:
        return None
    px = n["ltp"].astype(float)
    return {"high": float(px.max()), "low": float(px.min()), "close": float(px.iloc[-1])}


def _fmt(label, d):
    if not d or d.get("n", 0) == 0:
        return f"  {label:<12} n=0"
    edge = d.get("edge_vs_placebo")
    t = d.get("t")
    et = (f" · edge {edge:+.2f} t={t}" if edge is not None else "")
    return (f"  {label:<12} n={d['n']:<4} up/dn {d.get('up',0)}/{d['n']-d.get('up',0)} · "
            f"fade {d['fade_rate']*100:.0f}% · fwd {d['mean_fwd']:+.2f} (med {d['median_fwd']:+.2f}) · "
            f"mfe {d['mean_mfe']:+.2f}/mae {d['mean_mae']:+.2f} · hit {d.get('hit_net')}{et}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--days", help="comma-separated YYYY-MM-DD; default = all captured days")
    ap.add_argument("--margin", type=float)
    ap.add_argument("--thrust-s", type=float)
    ap.add_argument("--lookback", type=float, help="lookback_min for the rolling level")
    ap.add_argument("--horizon", type=float, help="horizon_min for the forward outcome")
    ap.add_argument("--barrier", type=float)
    args = ap.parse_args()

    days = args.days.split(",") if args.days else available_raw_days(args.symbol)
    if not days:
        print(f"No captured raw days for {args.symbol}."); return

    kw = {k: v for k, v in (("margin", args.margin), ("thrust_s", args.thrust_s),
                            ("lookback_min", args.lookback), ("horizon_min", args.horizon),
                            ("barrier", args.barrier)) if v is not None}
    cfg = Config(**kw)

    loaded, prev = [], {}
    all_days = available_raw_days(args.symbol)
    for day in days:
        df = load_raw_ticks(args.symbol, day)
        if df is None or getattr(df, "empty", True):
            continue
        loaded.append((day, df))
        # prior captured day (any earlier day we have), for prior-day H/L/C levels
        earlier = [d for d in all_days if d < day]
        if earlier:
            prev[day] = _prev_day_levels(load_raw_ticks(args.symbol, earlier[-1]))
    if not loaded:
        print("Nothing loaded."); return

    out = study(loaded, cfg, prev)
    c = out["cfg"]
    print(f"\nStop-run study · {args.symbol} · {out['n_days']} day(s): {', '.join(d for d, _ in loaded)}")
    print(f"  cfg: margin {c['margin']} · thrust {c['thrust_s']}s · lookback {c['lookback_min']}m · "
          f"horizon {c['horizon_min']}m · barrier {c['barrier']} · placebo n={out['placebo']['n']} "
          f"(mean {out['placebo']['mean']:+.2f})" if out['placebo']['n'] else "")
    print("\nOVERALL"); print(_fmt("all sweeps", out["overall"]))
    print("\nBY LEVEL TYPE")
    for lt, d in out["by_level"].items():
        print(_fmt(lt, d))
    print("\nBY SIGNATURE (does the tape's OI/depth read add anything?)")
    for sg, d in out["by_signature"].items():
        print(_fmt(sg, d))
    print("\n⚠️  Descriptive, placebo-gated. Trust nothing until |t| is large across many days "
          "(archive is ~2–3s snapshots; see docs/tick-data-ic-research.md).")


if __name__ == "__main__":
    main()
