"""Does dealer GAMMA predict intraday REALIZED VOLATILITY? (SqueezeMetrics, sign-aware)

The best-supported, sign-free-ish study we can run on existing data. Thesis
(SqueezeMetrics GEX white paper; see docs/research-agenda.md §3): aggregate dealer gamma
is inversely related to realized vol — dealers SHORT gamma hedge WITH the move (amplify →
high RV); LONG gamma hedge AGAINST it (dampen → low RV). Point-in-time: net_gex known at
t, realized vol measured over (t, t+H].

Two questions, one landmine-aware:
  1. |net_gex|  → forward RV   — does gamma CONCENTRATION predict vol?   (SIGN-FREE)
  2. net_gex    → forward RV   — the SIGNED test. net_gex is stored under the US model A
     (>0 = dealers long gamma). A NEGATIVE IC (more +gamma → less vol) is the US pattern
     (A holds); a POSITIVE IC (more +gamma → MORE vol) means the G+ bucket is the WILDER
     one → supports the INVERTED India dealer sign (model B). So this doubles as a
     dealer-sign test (cf. memory both-signs-dealer-sign-read).

Realized vol = std of 5-min log-returns over the forward window. We also report the
per-day-DE-MEANED IC (removes each day's vol regime) to separate the WITHIN-day gamma→vol
relation from the cross-day one.

  python scripts/analyze_gamma_vol.py            # NIFTY
  python scripts/analyze_gamma_vol.py BANKNIFTY

⚠️ Exploratory, decision-support only. Sign-free for the MAGNITUDE result; the SIGNED
result is landmine-relevant, read as evidence not proof. Not a trade rule."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.analyze_timeseries import load_all  # noqa: E402

HORIZONS_MIN = {"30m": 30, "1h": 60}
MIN_STEPS = 2                     # need >=2 forward returns to form an RV


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


def _events_for_day(day: str, df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty or "net_gex" not in df.columns or "spot" not in df.columns:
        return []
    df = df.drop_duplicates("time_ist").sort_values("time_ist").reset_index(drop=True)
    mm = [_mins(t) for t in df["time_ist"].tolist()]
    spot = pd.to_numeric(df["spot"], errors="coerce").to_numpy()
    gex = pd.to_numeric(df["net_gex"], errors="coerce").to_numpy()
    logret = np.diff(np.log(spot), prepend=np.nan)      # step log-return at each i (vs prev)
    ev = []
    for i in range(len(df)):
        if not (gex[i] == gex[i]) or not (spot[i] == spot[i]):
            continue
        rec = {"day": day, "g": float(gex[i]), "absg": abs(float(gex[i]))}
        ok = False
        for lbl, h in HORIZONS_MIN.items():
            # forward window = steps whose timestamp is in (t_i, t_i + h]
            js = [j for j in range(i + 1, len(df))
                  if mm[j] is not None and mm[i] is not None and mm[i] < mm[j] <= mm[i] + h]
            rv = float(np.nanstd([logret[j] for j in js])) * 100 if len(js) >= MIN_STEPS else np.nan
            rec["rv_" + lbl] = rv
            ok = ok or rv == rv
        if ok:
            ev.append(rec)
    return ev


def _ic(a, b):
    m = a.notna() & b.notna()
    return (a[m].corr(b[m], method="spearman"), int(m.sum())) if m.sum() >= 5 else (float("nan"), int(m.sum()))


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    days = load_all(symbol)
    events: list[dict] = []
    for d, df in sorted(days.items()):
        events.extend(_events_for_day(d, df))

    _print(f"Dealer gamma  ->  forward intraday realized vol  (point-in-time)  ·  {symbol}\n")
    _print(f"Days: {len(days)}   ·   snapshots with gamma+forward RV: {len(events)}")
    if len(events) < 10:
        _print("Too few — accrue more 5-min timeseries days, then re-run.")
        return

    df = pd.DataFrame(events)
    for lbl in HORIZONS_MIN:                             # per-day de-mean the RV (vol regime)
        df["rv_" + lbl + "_dm"] = df["rv_" + lbl] - df.groupby("day")["rv_" + lbl].transform("mean")

    _print("\nSpearman IC vs forward realized vol  [RAW | DE-MEANED per day]:")
    for lbl in HORIZONS_MIN:
        am_r, n = _ic(df["absg"], df["rv_" + lbl])
        am_d, _ = _ic(df["absg"], df["rv_" + lbl + "_dm"])
        sg_r, _ = _ic(df["g"], df["rv_" + lbl])
        sg_d, _ = _ic(df["g"], df["rv_" + lbl + "_dm"])
        _print(f"  {lbl:>3} (n={n}):  |gamma| {am_r:+.2f}|{am_d:+.2f}    signed gamma {sg_r:+.2f}|{sg_d:+.2f}")
    _print("  |gamma| = concentration (sign-free). signed: NEG ⇒ +gamma calmer (US model A);")
    _print("  POS ⇒ +gamma WILDER ⇒ inverted India sign (model B).")

    # Tercile of signed net_gex -> mean forward RV (the both-signs vol asymmetry).
    _print("\nForward RV (30m) by signed-gamma tercile:")
    g = df["g"]
    q1, q2 = g.quantile(1 / 3), g.quantile(2 / 3)
    for name, mask in [(f"G- (net_gex<{q1:+.1e})", g <= q1),
                       ("G0 (mid)", (g > q1) & (g < q2)),
                       (f"G+ (net_gex>{q2:+.1e})", g >= q2)]:
        s = df[mask]["rv_30m"].dropna()
        if len(s):
            _print(f"  {name:26s}: mean RV {s.mean():.3f}%  median {s.median():.3f}%  (n={len(s)})")

    _print("\n⚠️ EXPLORATORY — |gamma|→RV is sign-free; the signed result is dealer-sign evidence, "
           "not proof. RV = std of 5-min log-returns; overlapping windows autocorrelate. "
           "Re-run as capture accrues; cross-check the sign with analyze_timeseries both-signs.")


if __name__ == "__main__":
    main()
