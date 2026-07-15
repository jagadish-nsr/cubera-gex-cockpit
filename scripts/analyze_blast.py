"""Replay the TRUE-vs-FAKE gamma-blast score over captured expiry days.

For each near-money strike a day's spot CROSSES, we grade the crossing with
``app.gammablast.blast_score`` (sign-free: ITM-side writer covering + premium
expansion at the crossed strike, and the size/drift of the wall one strike ahead),
then compare that score to what spot ACTUALLY did over the next ``FWD_MIN`` minutes:

  realized continuation = how far beyond K spot travelled in the cross direction,
  sustained?            = spot at window end still beyond K (not reverted to the pin).

A useful score ⇒ higher score lines up with larger, sustained continuation. This is
CALIBRATION on a tiny sample (only a couple of fine-cadence expiry days exist yet) —
read the direction, never the decimals. Point-in-time: the score at a crossing uses
only OI/premium from the crossing forward; the realized move is measured after.

  python scripts/analyze_blast.py            # NIFTY, all captured days
  python scripts/analyze_blast.py BANKNIFTY

⚠️ Exploratory, decision-support only. Not a trade rule. The pin-vs-blast DIRECTION
still depends on the unvalidated India dealer sign (CLAUDE.md) — this grades a break's
STAYING POWER, sign-free; it does not predict up vs down."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gammablast import blast_score  # noqa: E402
from app.timeseries import OISTRIKES_DIR, load_strikes_day  # noqa: E402

FWD_MIN = 40          # minutes forward over which covering builds and we score continuation
BAND_PTS = 150.0      # only score strikes within this of the running spot (near-money)


def _mins(t: str):
    try:
        h, m = str(t).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _neighbor(strikes: list[float], k: float, direction: str):
    """Next listed strike in the travel direction (above for 'up', below for 'down')."""
    up = sorted(s for s in strikes if s > k)
    dn = sorted((s for s in strikes if s < k), reverse=True)
    return (up[0] if up else None) if direction == "up" else (dn[0] if dn else None)


def _leg(day: pd.DataFrame, k: float, t0: str, t1: str) -> list[dict]:
    """Time-ordered snapshot dicts for strike ``k`` over [t0, t1]."""
    d = day[(day["strike"] == k) & (day["time_ist"] >= t0) & (day["time_ist"] <= t1)]
    d = d.drop_duplicates("time_ist").sort_values("time_ist")
    cols = ["call_oi", "put_oi", "call_ltp", "put_ltp"]
    return [{c: r.get(c) for c in cols} for _, r in d.iterrows()]


def _crossings(day: pd.DataFrame) -> list[dict]:
    """Every first near-money strike crossing in the day: {time, strike, direction}."""
    t = sorted(day["time_ist"].unique())
    spot = day.groupby("time_ist")["spot"].first().reindex(t)
    strikes = sorted(float(k) for k in day["strike"].unique())
    seen: set[tuple[float, str]] = set()
    out = []
    for i in range(1, len(t)):
        s0, s1 = float(spot.iloc[i - 1]), float(spot.iloc[i])
        for k in strikes:
            if abs(k - s1) > BAND_PTS:
                continue
            if s0 < k <= s1:
                direction = "up"
            elif s0 > k >= s1:
                direction = "down"
            else:
                continue
            if (k, direction) in seen:            # only the first crossing each way
                continue
            seen.add((k, direction))
            out.append({"time": t[i], "strike": k, "direction": direction})
    return out


def _realized(day: pd.DataFrame, cross: dict) -> dict:
    """What spot did after the crossing: continuation beyond K and whether it held."""
    t = sorted(day["time_ist"].unique())
    spot = day.groupby("time_ist")["spot"].first().reindex(t)
    t0 = _mins(cross["time"])
    k, direction = cross["strike"], cross["direction"]
    fwd = [(tt, float(spot.loc[tt])) for tt in t if t0 <= (_mins(tt) or -1) <= t0 + FWD_MIN]
    if len(fwd) < 2:
        return {}
    sign = 1.0 if direction == "up" else -1.0
    beyond = [sign * (s - k) for _, s in fwd]     # +ve = on the continuation side of K
    end_beyond = beyond[-1]
    return {"max_cont": max(beyond), "end_cont": end_beyond,
            "sustained": end_beyond > 0, "end_time": fwd[-1][0]}


def analyze_day(date_ist: str, symbol: str) -> list[dict]:
    day = load_strikes_day(date_ist, symbol)
    if day is None or day.empty or "time_ist" not in day.columns:
        return []
    strikes = sorted(float(k) for k in day["strike"].unique())
    rows = []
    for cross in _crossings(day):
        t0 = cross["time"]
        t1 = next((t for t in sorted(day["time_ist"].unique())
                   if (_mins(t) or 0) >= (_mins(t0) or 0) + FWD_MIN), None)
        if t1 is None:                            # crossing too late in the day to score
            continue
        crossed = _leg(day, cross["strike"], t0, t1)
        nb = _neighbor(strikes, cross["strike"], cross["direction"])
        ahead = _leg(day, nb, t0, t1) if nb is not None else None
        sc = blast_score(cross["direction"], crossed, ahead)
        rl = _realized(day, cross)
        if not sc or not rl:
            continue
        rows.append({"date": date_ist, **cross, "next": nb, **sc, **rl})
    return rows


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    base = OISTRIKES_DIR / symbol
    days = sorted(p.stem for p in base.glob("*.csv")) if base.exists() else []
    rows: list[dict] = []
    for d in days:
        rows.extend(analyze_day(d, symbol))

    _print(f"True-vs-fake gamma-blast score  ·  {symbol}  ·  forward window {FWD_MIN}m\n")
    _print(f"Captured days: {days or '(none)'}   ·   scored crossings: {len(rows)}")
    if not rows:
        _print("No scorable near-money crossings — need fine-cadence expiry-day capture.")
        return

    _print("\ndate        cross  K        dir  score  grade                          "
           "cover  premΔ   wall×  | maxCont  endCont  held")
    for r in sorted(rows, key=lambda x: (x["date"], x["time"])):
        pe = f"{r['prem_exp']*100:+5.1f}%" if r["prem_exp"] is not None else "   —  "
        wr = f"{r['wall_ratio']:4.1f}" if r["wall_ratio"] is not None else "  — "
        _print(f"{r['date']}  {r['time']}  {r['strike']:>8.0f}  {r['direction']:<4} "
               f"{r['score']:>4}   {r['grade']:<32} {r['cover_frac']*100:+5.1f}% {pe} "
               f"{wr}  | {r['max_cont']:+7.0f}  {r['end_cont']:+7.0f}  "
               f"{'YES' if r['sustained'] else 'no'}")

    df = pd.DataFrame(rows)
    if len(df) >= 3:
        ic = df["score"].corr(df["end_cont"], method="spearman")
        _print(f"\nSpearman(score, end continuation) = {ic:+.2f}  (n={len(df)}) — "
               "positive ⇒ higher score tracks a move that held past K.")

        # Regime split by expiry-STRUCTURE era. This live-capture sample is TINY, so a split is
        # mostly a robustness/where-are-the-days check, not a per-era statistic to trust.
        from app.expiry_regime import ERA_LABELS, era_order, expiry_era
        df["era"] = df["date"].map(expiry_era)
        _print("  by expiry-structure era (thin — read counts, not decimals):")
        for e in era_order(df["era"].unique()):
            g = df[df["era"] == e]
            eic = (f"{g['score'].corr(g['end_cont'], method='spearman'):+.2f}"
                   if len(g) >= 8 else "n/a")
            _print(f"    {ERA_LABELS[e]:<42} n={len(g):>3} ({g['date'].nunique()} days)  IC={eic}")
    _print("\n⚠️ Tiny sample, calibration only. Read the SIGN, not the decimals. "
           "Sign-free staying-power grade; direction is NOT claimed. Re-run as expiry "
           "days accrue (the 1-min gamma_blast capture will add real blast days).")


if __name__ == "__main__":
    main()
