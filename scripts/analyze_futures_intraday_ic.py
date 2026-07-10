"""Does the INTRADAY Nifty-futures build-up predict the forward intraday return?

The core hypothesis behind the futures+options strategy: intraday futures OI+price
build-up is a **sign-free DIRECTION** signal (unlike options GEX, which is stuck behind
the dealer-sign landmine). This validates it point-in-time, the same discipline as the
streak / EOD-futures IC studies.

At each snapshot t (with enough history), we read the build-up over the trailing
``TRAIL_MIN`` minutes — ΔPrice(near) × ΔOI(aggregate, rollover-netted) → a state — and
measure the forward near-month futures return at 15 / 30 / 60 min. Point-in-time: the
signal uses only [t−TRAIL, t]; the return is realised after t. Signals tested:

  momentum   sign(trailing ΔPrice)                      [baseline]
  bias       +1 bullish / −1 bearish build-up state
  fresh      +1 long build-up / −1 short build-up / 0 covering-or-unwinding
  raw ΔOI    trailing aggregate ΔOI

  python scripts/analyze_futures_intraday_ic.py            # NIFTY
  python scripts/analyze_futures_intraday_ic.py BANKNIFTY

⚠️ SCAFFOLD — intraday futures capture is forward-only and just started, so this prints
"too few events" until a couple of weeks accrue. Read the SIGN, never the decimals; tiny,
autocorrelated. Sign-free, decision-support only, NOT a trade rule.

TODO once direction validates: add the futures×options AGREEMENT test — does gating the
futures-direction signal by options-footprint agreement (put/call ΔOI skew, walls) improve
the IC? That's the resilient-strategy combination; it needs joining the oi_strikes journal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.futures_buildup import (FUTURES_INTRADAY_DIR, buildup_bias,  # noqa: E402
                                 classify_futures_buildup, load_futures_intraday_day)

TRAIL_MIN = 15
HORIZONS_MIN = {"15m": 15, "30m": 30, "1h": 60}
_FRESH = {"long build-up": 1, "short build-up": -1, "short covering": 0, "long unwinding": 0}


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


def _idx_at_or_after(mm, i, delta):
    tgt = mm[i] + delta
    for j in range(i + 1, len(mm)):
        if mm[j] is not None and mm[j] >= tgt:
            return j
    return None


def _idx_at_or_before(mm, i, delta):
    tgt = mm[i] - delta
    best = None
    for j in range(i - 1, -1, -1):
        if mm[j] is not None and mm[j] <= tgt:
            best = j
            break
    return best


def _bias_sign(label):
    b = buildup_bias(label)
    return 1 if b and b.startswith("bull") else -1 if b and b.startswith("bear") else 0


def _events_for_day(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty or "time_ist" not in df.columns:
        return []
    df = df.drop_duplicates("time_ist").sort_values("time_ist")
    mm = [_mins(t) for t in df["time_ist"].tolist()]
    price = pd.to_numeric(df["price"], errors="coerce").to_numpy()
    oi = pd.to_numeric(df["agg_oi"], errors="coerce").to_numpy()
    ev = []
    for i in range(len(df)):
        b = _idx_at_or_before(mm, i, TRAIL_MIN)
        if b is None or not price[i] or price[i] != price[i]:
            continue
        dp, do = price[i] - price[b], oi[i] - oi[b]
        if not (dp == dp and do == do):
            continue
        state = classify_futures_buildup(dp, do)
        rec = {"state": state, "mom": float(np.sign(dp)), "bias": _bias_sign(state),
               "fresh": _FRESH.get(state, 0), "doi": float(do)}
        ok = False
        for lbl, h in HORIZONS_MIN.items():
            j = _idx_at_or_after(mm, i, h)
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
        for e in _events_for_day(load_futures_intraday_day(d, symbol)):
            e["day"] = d                              # tag for per-day de-trending
            events.append(e)

    _print(f"Intraday futures build-up ({TRAIL_MIN}m trailing)  ->  forward futures return "
           f"at 15m/30m/1h  (point-in-time)  ·  {symbol}\n")
    _print(f"Captured intraday days: {days or '(none)'}   ·   events: {len(events)}")
    if len(events) < 5:
        _print("Too few events yet — intraday futures capture is forward-only and just started. "
               "Re-run after ~1-2 weeks of 5-min capture accrues.")
        return

    df = pd.DataFrame(events)
    # DE-TREND: subtract each day's mean forward return so we test the SIGNAL, not the
    # day's drift (a one-directional window otherwise biases every forward return and
    # can fake or hide a signal). IC on the residual is the honest read.
    for lbl in HORIZONS_MIN:
        df[lbl + "_dt"] = df[lbl] - df.groupby("day")[lbl].transform("mean")

    _print("\nSpearman IC vs forward return  [RAW | DE-TRENDED (per-day drift removed)]:")
    for lbl in HORIZONS_MIN:
        row = []
        for sig in ("mom", "bias", "fresh", "doi"):
            raw, n = _ic(df[sig], df[lbl])
            det, _ = _ic(df[sig], df[lbl + "_dt"])
            row.append(f"{sig if sig != 'doi' else 'rawΔOI'} {raw:+.2f}|{det:+.2f}")
        _print(f"  {lbl:>3} (n={n}):  " + "   ".join(row))
    _print("  (each cell RAW|DET — DET removes the day's drift; read DET as the signal.)")

    _print("\nDE-TRENDED 30m forward return by build-up state (day drift removed):")
    for st in ("long build-up", "short covering", "short build-up", "long unwinding"):
        s = df[df["state"] == st]["30m_dt"].dropna()
        if len(s):
            _print(f"  {st:16s} ({str(buildup_bias(st) or '-')[:12]:12s}): mean {s.mean():+.3f}%  "
                   f"P(>0) {(s > 0).mean()*100:.0f}%  (n={len(s)})")

    # BUILD-UP ONLY — the user's strategy focus: fresh-money trends (long/short build-up),
    # ignoring short covering / long unwinding (position-squaring / short-term events).
    bu = df[df["state"].isin(["long build-up", "short build-up"])]
    _print(f"\nBUILD-UP ONLY (fresh trends: long/short build-up, n={len(bu)}) — de-trended:")
    for lbl in HORIZONS_MIN:
        det, n = _ic(bu["bias"], bu[lbl + "_dt"])
        r = (bu["bias"] * bu[lbl + "_dt"]).dropna()   # return in the signal's direction
        hit = f"dir-return mean {r.mean():+.3f}%  P(>0) {(r > 0).mean()*100:.0f}%" if len(r) else "—"
        _print(f"  {lbl:>3}:  bias IC {det:+.2f}   {hit}   (n={n})")

    _print("\n⚠️ EXPLORATORY — tiny n, overlapping windows autocorrelate. The bias sign tracks "
           "sign(ΔPrice) so its IC ≈ momentum; the question is whether the OI dimension "
           "(fresh-money build-up) adds edge. Sign-free; NOT a trade rule. Re-run as capture accrues.")


if __name__ == "__main__":
    main()
