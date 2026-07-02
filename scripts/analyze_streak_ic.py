"""If we bought the NAKED option when a 3×15-min build-up streak fired, would it pay?

Point-in-time IC for the intraday streak signal (`app/oi_footprint.intraday_buildup_streaks`
logic). At the snapshot where a strike's per-interval build-up colour first reaches a run of
``RUN`` (green = long build-up OI↑&prem↑, red = writing OI↑&prem↓), we "buy the naked option"
at that snapshot's premium and measure the forward premium return one 15-min step later
(long-only: the option's own % change). Signal = +1 green / −1 red. IC = Spearman rank corr
between signal and forward return; a positive IC ⇒ the streak has momentum (green→premium up,
red→premium down) so a long-on-green / avoid-on-red trade has edge.

  python scripts/analyze_streak_ic.py

⚠️ Exploratory, decision-support only. Only a few captured days exist and 15-min option
premia are noisy — read the SIGN and the hit-rate, never the magnitude, until n grows.
Overlapping strikes/days autocorrelate; one event per streak ONSET limits it but doesn't
remove it. Not a trade rule. Premium = nearest-expiry LTP; ΔOI = all-expiry, step-over-step."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.oi_footprint import classify_buildup  # noqa: E402
from app.timeseries import OISTRIKES_DIR, load_strikes_day  # noqa: E402

RUN = 3
TOL_PREM_PCT = 0.5
MIN_CUM_DOI = 2000.0
BAND_FRAC = 0.03
SYMBOL = "NIFTY"


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _grid15(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse a day's snapshots onto a 15-min grid: keep the FIRST snapshot in each
    15-min bucket (clean 15-min days pass through unchanged; finer days downsample)."""
    def _mins(t):
        try:
            h, m = str(t).split(":"); return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return None
    df = df.copy()
    df["_bucket"] = df["time_ist"].map(lambda t: (_mins(t) // 15) if _mins(t) is not None else None)
    keep = df.dropna(subset=["_bucket"]).sort_values("time_ist").drop_duplicates(["_bucket", "strike"])
    return keep.drop(columns=["_bucket"])


def _events_for_day(df: pd.DataFrame) -> list[dict]:
    """Streak-onset events for one gridded day: at the snapshot where a strike/side's
    build-up colour first reaches a run of RUN, record the signal (+1 green/−1 red) and
    the forward 1-step naked-long option return (buy at run-end, sell next snapshot)."""
    if df is None or df.empty or "time_ist" not in df.columns:
        return []
    df = _grid15(df)
    spot = pd.to_numeric(df["spot"], errors="coerce").dropna()
    if spot.empty:
        return []
    spot = float(spot.iloc[-1])
    band = spot * BAND_FRAC
    ev: list[dict] = []
    for side in ("call", "put"):
        oi_col, ltp_col = f"{side}_oi", f"{side}_ltp"
        if oi_col not in df.columns or ltp_col not in df.columns:
            continue
        for k, sub in df.groupby("strike"):
            k = float(k)
            if abs(k - spot) > band:
                continue
            sub = sub.drop_duplicates("time_ist").sort_values("time_ist")
            oi = pd.to_numeric(sub[oi_col], errors="coerce").to_numpy()
            ltp = pd.to_numeric(sub[ltp_col], errors="coerce").to_numpy()
            m = len(sub)
            if m < RUN + 2:                        # need run + a forward step
                continue
            kinds = []
            for i in range(1, m):
                doi = oi[i] - oi[i - 1]
                pp = (ltp[i] / ltp[i - 1] - 1.0) * 100.0 if ltp[i - 1] else np.nan
                lab = classify_buildup(doi, pp, tol_prem=TOL_PREM_PCT)
                kinds.append("green" if lab == "long build-up"
                             else "red" if lab == "writing (short build-up)" else None)
            # interval i spans snaps [i, i+1]. Onset: trailing run first hits RUN at
            # interval i (i.e. kinds[i-RUN+1..i] equal AND the one before differs).
            for i in range(RUN - 1, len(kinds)):
                kind = kinds[i]
                if kind is None or any(kinds[i - r] != kind for r in range(RUN)):
                    continue
                if i - RUN >= 0 and kinds[i - RUN] == kind:
                    continue                       # not the onset (run already ≥RUN+1)
                buy_idx = i + 1                     # snapshot at run end = buy price
                if buy_idx + 1 >= m:                # need a forward step to sell
                    continue
                cum_doi = float(oi[buy_idx] - oi[i - RUN + 1])
                if abs(cum_doi) < MIN_CUM_DOI:
                    continue
                buy, sell = ltp[buy_idx], ltp[buy_idx + 1]
                if not (buy and buy == buy and sell == sell) or buy <= 0:
                    continue
                ev.append({"side": side, "strike": k, "kind": kind,
                           "signal": 1 if kind == "green" else -1,
                           "fwd_ret_pct": (sell / buy - 1.0) * 100.0})
    return ev


def main() -> None:
    base = OISTRIKES_DIR / SYMBOL
    days = sorted(p.stem for p in base.glob("*.csv")) if base.exists() else []
    events: list[dict] = []
    for d in days:
        events.extend(_events_for_day(load_strikes_day(d, SYMBOL)))

    _print("Intraday 3×15-min build-up streak  ->  naked LONG option, next-step return "
           "(point-in-time)\n")
    _print(f"Captured days: {days or '(none)'}   ·   streak events: {len(events)}")
    if len(events) < 3:
        _print("Too few events for an IC — accrue more intraday capture days, then re-run.")
        return

    df = pd.DataFrame(events)
    ic = df["signal"].corr(df["fwd_ret_pct"], method="spearman")
    _print(f"\nSpearman IC (signal ∈ +1 green / −1 red  vs  forward long-option %): "
           f"{ic:+.2f}   (n={len(df)})")

    for kind in ("green", "red"):
        sub = df[df["kind"] == kind]["fwd_ret_pct"]
        if len(sub):
            hit = (sub > 0).mean() * 100
            _print(f"  {kind:>5} streaks (n={len(sub):>3}): long-option next-step "
                   f"mean {sub.mean():+.1f}%  median {sub.median():+.1f}%  "
                   f"P(return>0) {hit:.0f}%")

    green = df[df["kind"] == "green"]["fwd_ret_pct"]
    if len(green):
        _print(f"\nLong-ONLY on GREEN streaks (the actual naked-long trade): n={len(green)}, "
               f"win-rate {(green > 0).mean()*100:.0f}%, mean {green.mean():+.1f}% per step.")
    _print("\n⚠️ EXPLORATORY — tiny n, 15-min option premia are noisy, overlapping "
           "strikes/days autocorrelate. Read the SIGN and hit-rate only; do NOT trade this. "
           "A positive IC ⇒ streaks show short-horizon MOMENTUM; negative ⇒ they MEAN-REVERT "
           "(the naked-long-on-green trade would lose). Re-run as capture accrues.")


if __name__ == "__main__":
    main()
