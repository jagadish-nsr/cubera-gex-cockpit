"""Does FUTURES direction + OPTIONS-footprint AGREEMENT beat futures direction alone?

The resilient-strategy thesis: intraday futures build-up gives a sign-free DIRECTION;
the options per-strike ΔOI footprint gives an independent positioning bias (put-writing
dominant = support/bullish; call-writing dominant = resistance/bearish). When the two
AGREE, conviction is higher — so gating the futures signal by agreement should improve
the forward edge over futures alone.

For each snapshot t (futures + options captured lock-step by the same task), we read:
  futures_bias  = build-up bias over the trailing window (ΔPrice × aggregate ΔOI)
  options_bias  = sign(net_skew)  [put-adds − call-adds near spot; >0 = bullish support]
  agree         = futures_bias · options_bias > 0
  combined      = futures_bias when they agree, else 0 (stand aside)
and the DE-TRENDED forward futures return at 15/30/60m (per-day drift removed, so we
test the signal not the day's trend). Point-in-time; sign-free (net_skew is raw OI, not
gamma — no dealer-sign landmine; the put-build=support mapping is a heuristic).

  python scripts/analyze_futures_options_agreement.py            # NIFTY

⚠️ SCAFFOLD — intraday capture just started; prints "too few" until it accrues. Tiny,
overlapping, autocorrelated. Read the SIGN only. NOT a trade rule."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.futures_buildup import (FUTURES_INTRADAY_DIR, buildup_bias,  # noqa: E402
                                 classify_futures_buildup, load_futures_intraday_day)
from app.oi_footprint import footprint_series  # noqa: E402
from app.timeseries import load_strikes_day  # noqa: E402

TRAIL_MIN = 15
HORIZONS_MIN = {"15m": 15, "30m": 30, "1h": 60}
BAND_FRAC = 0.03


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _mins(t):
    try:
        h, m = str(t).split(":"); return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _fwd(mm, i, h):
    tgt = mm[i] + h
    for j in range(i + 1, len(mm)):
        if mm[j] is not None and mm[j] >= tgt:
            return j
    return None


def _before(mm, i, delta):
    tgt = mm[i] - delta
    for j in range(i - 1, -1, -1):
        if mm[j] is not None and mm[j] <= tgt:
            return j
    return None


def _fut_bias(state):
    b = buildup_bias(state)
    return 1 if b and b.startswith("bull") else -1 if b and b.startswith("bear") else 0


def _events_for_day(day: str, symbol: str) -> list[dict]:
    fut = load_futures_intraday_day(day, symbol)
    strikes = load_strikes_day(day, symbol)
    if fut is None or fut.empty or strikes is None or strikes.empty:
        return []
    # options net_skew per snapshot time (sign-free footprint bias)
    skew = {t: ns for t, _sup, _res, ns in footprint_series(strikes, band_frac=BAND_FRAC)}
    fut = fut.drop_duplicates("time_ist").sort_values("time_ist")
    mm = [_mins(t) for t in fut["time_ist"].tolist()]
    times = fut["time_ist"].tolist()
    price = pd.to_numeric(fut["price"], errors="coerce").to_numpy()
    oi = pd.to_numeric(fut["agg_oi"], errors="coerce").to_numpy()
    ev = []
    for i in range(len(fut)):
        b = _before(mm, i, TRAIL_MIN)
        ns = skew.get(times[i])
        if b is None or ns is None or not price[i] or price[i] != price[i]:
            continue
        dp, do = price[i] - price[b], oi[i] - oi[b]
        if not (dp == dp and do == do):
            continue
        state = classify_futures_buildup(dp, do)
        fb = _fut_bias(state)
        ob = 1 if ns > 0 else -1 if ns < 0 else 0
        if fb == 0 or ob == 0:
            continue
        rec = {"day": day, "state": state, "fut": fb, "opt": ob,
               "agree": 1 if fb * ob > 0 else 0, "combined": fb if fb * ob > 0 else 0}
        ok = False
        for lbl, h in HORIZONS_MIN.items():
            j = _fwd(mm, i, h)
            rec[lbl] = (price[j] / price[i] - 1.0) * 100.0 if (j is not None and price[i]) else np.nan
            ok = ok or rec[lbl] == rec[lbl]
        if ok:
            ev.append(rec)
    return ev


def _ic(a, b):
    m = a.notna() & b.notna()
    return (a[m].corr(b[m], method="spearman"), int(m.sum())) if m.sum() >= 5 else (float("nan"), int(m.sum()))


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    base = FUTURES_INTRADAY_DIR / symbol
    days = sorted(p.stem for p in base.glob("*.csv")) if base.exists() else []
    events: list[dict] = []
    for d in days:
        events.extend(_events_for_day(d, symbol))

    _print(f"Futures direction × options-footprint AGREEMENT  ->  forward return  ·  {symbol}\n")
    _print(f"Days: {days or '(none)'}   ·   joined events: {len(events)}")
    if len(events) < 8:
        _print("Too few joined events yet — needs intraday futures AND oi_strikes for the same "
               "snapshots to accrue (~1-2 weeks). Re-run later.")
        return

    df = pd.DataFrame(events)
    for lbl in HORIZONS_MIN:                          # de-trend forward returns per day
        df[lbl + "_dt"] = df[lbl] - df.groupby("day")[lbl].transform("mean")
    agree_rate = df["agree"].mean() * 100
    _print(f"agreement rate: {agree_rate:.0f}%  ({int(df['agree'].sum())}/{len(df)} snapshots)\n")

    _print("DE-TRENDED IC vs forward return  [futures alone | options alone | AGREEMENT-gated]:")
    for lbl in HORIZONS_MIN:
        fi, n = _ic(df["fut"], df[lbl + "_dt"])
        oi_, _ = _ic(df["opt"], df[lbl + "_dt"])
        ci, _ = _ic(df["combined"], df[lbl + "_dt"])
        _print(f"  {lbl:>3} (n={n}):  futures {fi:+.2f}   options {oi_:+.2f}   AGREE-gated {ci:+.2f}")

    _print("\nFutures-directional de-trended 30m return — AGREE vs DISAGREE with options:")
    for name, sub in [("AGREE   ", df[df["agree"] == 1]), ("DISAGREE", df[df["agree"] == 0])]:
        r = (sub["fut"] * sub["30m_dt"]).dropna()    # return in the futures-signal direction
        if len(r):
            _print(f"  {name}: mean {r.mean():+.3f}%  P(>0) {(r > 0).mean()*100:.0f}%  (n={len(r)})")

    # BUILD-UP ONLY (the strategy focus): fresh trends, agree vs disagree.
    bu = df[df["state"].isin(["long build-up", "short build-up"])]
    _print(f"\nBUILD-UP ONLY (fresh trends, n={len(bu)}) — futures-directional 30m de-trended:")
    for name, sub in [("AGREE   ", bu[bu["agree"] == 1]), ("DISAGREE", bu[bu["agree"] == 0])]:
        r = (sub["fut"] * sub["30m_dt"]).dropna()
        if len(r):
            _print(f"  {name}: mean {r.mean():+.3f}%  P(>0) {(r > 0).mean()*100:.0f}%  (n={len(r)})")
    _print("\n⚠️ SCAFFOLD — tiny n, overlapping/autocorrelated, sign-free. If AGREE-gated IC and "
           "the AGREE hit-rate beat futures-alone as n grows, the combination has legs. NOT a "
           "trade rule. net_skew put-build=support is a heuristic. Re-run as capture accrues.")


if __name__ == "__main__":
    main()
