"""If we bought the NAKED option when a live build-up streak fired, would it pay?

Point-in-time IC for the intraday streak signal on **native capture cadence** (matching the
live dashboard detector, `app/oi_footprint.intraday_buildup_streaks(only_live=True)`), NOT a
15-min grid. At the snapshot where a strike's per-interval build-up colour first reaches a run
of ``RUN`` — the trailing/LIVE run at that instant (green = long build-up OI↑&prem↑, red =
writing OI↑&prem↓) — we "buy the naked option" at run-end and measure the forward premium
return at 15m / 30m / 1h ahead by TIME. Signal = +1 green / −1 red. IC = Spearman rank corr
between signal and forward return per horizon; a positive IC ⇒ the streak has premium momentum
(long-on-green pays), negative ⇒ mean-reversion. Also splits by run TURNOVER (volume ÷ OI, the
dashboard's conviction axis) to check whether high-conviction live streaks behave differently.

  python scripts/analyze_streak_ic.py            # NIFTY
  python scripts/analyze_streak_ic.py BANKNIFTY

⚠️ Exploratory, decision-support only. Few captured days and intraday option premia are noisy
— read the SIGN and the hit-rate, never the magnitude, until n grows. Overlapping strikes/days
autocorrelate; one event per streak ONSET limits it but doesn't remove it. Not a trade rule.
Premium = nearest-expiry LTP; ΔOI = all-expiry, step-over-step."""

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
# Forward horizons in MINUTES (native cadence): label -> minutes ahead.
HORIZONS_MIN = {"15m": 15, "30m": 30, "1h": 60}


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


def _fwd_idx(mm: list, i: int, h: int):
    """First snapshot index at/after mm[i] + h minutes; None if the day ends first."""
    target = mm[i] + h
    for j in range(i + 1, len(mm)):
        if mm[j] is not None and mm[j] >= target:
            return j
    return None


def _events_for_day(df: pd.DataFrame) -> list[dict]:
    """Live streak-onset events for one day at native cadence: at the snapshot where a
    strike/side's build-up colour first reaches a run of RUN (the trailing run still
    building at that instant), record the signal (+1 green/−1 red), the run's turnover
    (volume ÷ OI), and the forward naked-long option return at each time horizon."""
    if df is None or df.empty or "time_ist" not in df.columns:
        return []
    spot = pd.to_numeric(df["spot"], errors="coerce").dropna()
    if spot.empty:
        return []
    spot = float(spot.iloc[-1])
    band = spot * BAND_FRAC
    has_vol = "call_vol" in df.columns
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
            mm = [_mins(x) for x in sub["time_ist"].tolist()]
            oi = pd.to_numeric(sub[oi_col], errors="coerce").to_numpy()
            ltp = pd.to_numeric(sub[ltp_col], errors="coerce").to_numpy()
            vol = pd.to_numeric(sub[f"{side}_vol"], errors="coerce").to_numpy() if has_vol else None
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
            # interval i (kinds[i-RUN+1..i] equal AND the one before differs).
            for i in range(RUN - 1, len(kinds)):
                kind = kinds[i]
                if kind is None or any(kinds[i - r] != kind for r in range(RUN)):
                    continue
                if i - RUN >= 0 and kinds[i - RUN] == kind:
                    continue                       # not the onset (run already ≥RUN+1)
                buy_idx = i + 1                     # snapshot at run end = buy price
                start_idx = i - RUN + 1
                if abs(oi[buy_idx] - oi[start_idx]) < MIN_CUM_DOI:
                    continue
                buy = ltp[buy_idx]
                if not (buy and buy == buy) or buy <= 0:
                    continue
                turn = np.nan
                if vol is not None and oi[buy_idx] and vol[buy_idx] == vol[buy_idx] \
                        and vol[start_idx] == vol[start_idx]:
                    turn = (vol[buy_idx] - vol[start_idx]) / oi[buy_idx]
                rec = {"side": side, "strike": k, "kind": kind,
                       "signal": 1 if kind == "green" else -1, "turn": turn}
                ok = False
                for lbl, h in HORIZONS_MIN.items():  # forward long-option return per horizon
                    j = _fwd_idx(mm, buy_idx, h)
                    sell = ltp[j] if j is not None else np.nan
                    rec[lbl] = (sell / buy - 1.0) * 100.0 if sell == sell else np.nan
                    ok = ok or rec[lbl] == rec[lbl]
                if ok:
                    ev.append(rec)
    return ev


def _ic(sub: pd.DataFrame, lbl: str) -> tuple:
    s = sub[["signal", lbl]].dropna()
    ic = s["signal"].corr(s[lbl], method="spearman") if len(s) >= 5 else float("nan")
    return ic, len(s)


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    base = OISTRIKES_DIR / symbol
    days = sorted(p.stem for p in base.glob("*.csv")) if base.exists() else []
    events: list[dict] = []
    for d in days:
        events.extend(_events_for_day(load_strikes_day(d, symbol)))

    _print(f"Intraday LIVE build-up streak (native cadence)  ->  naked LONG option, forward "
           f"return at 15m / 30m / 1h  (point-in-time)  ·  {symbol}\n")
    _print(f"Captured days: {days or '(none)'}   ·   live-streak onsets: {len(events)}")
    if len(events) < 5:
        _print("Too few events for an IC — accrue more intraday capture days, then re-run.")
        return

    df = pd.DataFrame(events)
    _print(f"  (green {int((df.signal > 0).sum())}, red {int((df.signal < 0).sum())})")
    _print("\nSpearman IC  (signal ∈ +1 green / −1 red  vs  forward long-option %):")
    for lbl in HORIZONS_MIN:
        ic, n = _ic(df, lbl)
        _print(f"  {lbl:>3}:  IC {ic:+.2f}   (n={n})")

    for kind in ("green", "red"):
        _print(f"\n{kind.upper()} live streaks — naked-long forward return:")
        for lbl in HORIZONS_MIN:
            s = df[df["kind"] == kind][lbl].dropna()
            if len(s):
                _print(f"  {lbl:>3}:  mean {s.mean():+.1f}%  median {s.median():+.1f}%  "
                       f"P(return>0) {(s > 0).mean()*100:.0f}%   (n={len(s)})")

    # Turnover split: do HIGH-conviction live streaks (the dashboard's ranking) differ?
    turns = df["turn"].dropna()
    if len(turns) >= 10:
        tv = float(turns.median())
        _print(f"\n--- split by run turnover (median {tv:.2f}×OI = conviction) ---")
        for name, mask in [("HI turnover", df["turn"] >= tv), ("LO turnover", df["turn"] < tv)]:
            sub = df[mask]
            line = "   ".join(f"{lbl} IC {_ic(sub, lbl)[0]:+.2f}" for lbl in HORIZONS_MIN)
            _print(f"  {name} (n={len(sub)}):  {line}")

    _print("\n⚠️ EXPLORATORY — tiny n, intraday premia are noisy, overlapping strikes/days "
           "autocorrelate. Read the SIGN and hit-rate only; do NOT trade this. Positive IC ⇒ "
           "MOMENTUM (long-on-green pays); negative ⇒ MEAN-REVERSION. GREEN = the actual "
           "naked-long trade; RED points at the writer side. Turnover ranks conviction, not "
           "direction. Re-run as capture accrues.")


if __name__ == "__main__":
    main()
