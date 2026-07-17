"""Does spot get PINNED toward the magnet / max-pain strike? (Ni-Pearson-Poteshman)

Study #2 from docs/research-agenda.md §9. Thesis: delta-hedging of large open interest
pulls spot TOWARD high-OI strikes, strongest into expiry (JFE 2005; ~0.65% pin effect).
SIGN-FREE — it's about OI concentration, not the dealer-gamma sign (dodges the landmine).

Point-in-time at each snapshot t: gap = spot − pin (pin = magnet or max_pain, as of t);
if pinning holds, the forward move is pulled toward the pin, i.e. gap NEGATIVELY predicts
the forward move (spot above pin → comes down; below → goes up). We report Spearman
IC(gap, forward move); a NEGATIVE IC = pin pull. Horizons 30m / 1h / to-close, raw and
per-day de-trended (remove the day's drift). Cuts: expiry vs non-expiry, afternoon vs
morning (pinning strengthens late), and by |gamma| concentration tercile (stronger pull
when more OI is stacked). Nifty weekly expiry = Tuesday.

  python scripts/analyze_pinning.py            # NIFTY

⚠️ Exploratory, sign-free, decision-support only. Only ~2 expiry days captured so far, so
the expiry-specific cut is anecdotal — read the ALL-days pull and re-run as expiries accrue.
Not a trade rule."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.expiry import next_weekly_expiry  # noqa: E402
from scripts.analyze_timeseries import load_all  # noqa: E402

HORIZONS_MIN = {"30m": 30, "1h": 60}
CLOSE_M = 15 * 60 + 30
NOON_M = 12 * 60 + 22            # session midpoint (09:15–15:30) → morning/afternoon split


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


def _is_expiry(d: str) -> bool:
    dt = date.fromisoformat(d)
    return next_weekly_expiry(dt) == dt


def _events_for_day(d: str, df: pd.DataFrame) -> list[dict]:
    need = {"spot", "magnet", "max_pain", "net_gex"}
    if df is None or df.empty or not need.issubset(df.columns):
        return []
    df = df.drop_duplicates("time_ist").sort_values("time_ist").reset_index(drop=True)
    mm = [_mins(t) for t in df["time_ist"].tolist()]
    spot = pd.to_numeric(df["spot"], errors="coerce").to_numpy()
    mag = pd.to_numeric(df["magnet"], errors="coerce").to_numpy()
    mp = pd.to_numeric(df["max_pain"], errors="coerce").to_numpy()
    gex = pd.to_numeric(df["net_gex"], errors="coerce").to_numpy()
    close_spot = spot[-1]
    ev = []
    for i in range(len(df)):
        if not (spot[i] == spot[i]) or mm[i] is None:
            continue
        # CONTROL anchor: trailing 60-min mean of spot. If gap→move is just as negative
        # here as to the magnet, it's GENERIC mean-reversion, not OI pinning.
        tw = [spot[j] for j in range(i + 1) if mm[j] is not None and mm[i] - mm[j] <= 60]
        trail = float(np.nanmean(tw)) if tw else np.nan
        rec = {"day": d, "expiry": _is_expiry(d), "mins_to_close": CLOSE_M - mm[i],
               "afternoon": mm[i] >= NOON_M, "absg": abs(float(gex[i])) if gex[i] == gex[i] else np.nan,
               "gap_mag": float(spot[i] - mag[i]) if mag[i] == mag[i] else np.nan,
               "gap_mp": float(spot[i] - mp[i]) if mp[i] == mp[i] else np.nan,
               "gap_trail": float(spot[i] - trail) if trail == trail else np.nan}
        for lbl, h in HORIZONS_MIN.items():
            js = [j for j in range(i + 1, len(df)) if mm[j] is not None and mm[i] < mm[j] <= mm[i] + h]
            rec["mv_" + lbl] = float(spot[js[-1]] - spot[i]) if js else np.nan
        rec["mv_close"] = float(close_spot - spot[i]) if i < len(df) - 1 else np.nan
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
    exp_days = sorted(d for d in days if _is_expiry(d))

    _print(f"Pinning: spot → magnet / max-pain  (gap negatively predicts move = pull)  ·  {symbol}\n")
    _print(f"Days: {len(days)}  ·  expiry days: {exp_days or '(none)'}  ·  snapshots: {len(events)}")
    if len(events) < 10:
        _print("Too few — accrue more 5-min days, then re-run.")
        return

    df = pd.DataFrame(events)
    mvcols = ["mv_30m", "mv_1h", "mv_close"]
    for c in mvcols:                                    # de-trend: remove each day's drift
        df[c + "_dt"] = df[c] - df.groupby("day")[c].transform("mean")

    def _block(title, sub):
        _print(f"\n{title}  (n={len(sub)}):")
        for pin, gcol in [("magnet", "gap_mag"), ("max_pain", "gap_mp"), ("trail-mean*", "gap_trail")]:
            cells = []
            for c in mvcols:
                ic, n = _ic(sub[gcol], sub[c + "_dt"])
                cells.append(f"{c.replace('mv_','')} {ic:+.2f}")
            _print(f"  gap→move [{pin:8s}] de-trended IC:  " + "   ".join(cells)
                   + "   (neg = pull toward pin)")

    _block("ALL days", df)
    _block("EXPIRY days only (anecdotal, tiny)", df[df["expiry"]])
    _block("NON-EXPIRY days", df[~df["expiry"]])
    _block("AFTERNOON (≥12:22, pinning strengthens late)", df[df["afternoon"]])
    _print("  * trail-mean = CONTROL (60-min spot SMA). Magnet/max-pain must beat it to be "
           "OI pinning, not generic mean-reversion.")

    # Convergence: does |gap to magnet| shrink from first hour to last hour?
    _print("\nConvergence — mean |spot − magnet| (points), first hour vs last hour:")
    for name, sub in [("all", df), ("expiry", df[df["expiry"]]), ("non-expiry", df[~df["expiry"]])]:
        early = sub[sub["mins_to_close"] > 5 * 60]["gap_mag"].abs().dropna()
        late = sub[sub["mins_to_close"] <= 60]["gap_mag"].abs().dropna()
        if len(early) and len(late):
            _print(f"  {name:11s}: first-hr {early.mean():5.1f}  →  last-hr {late.mean():5.1f}  "
                   f"({'converges' if late.mean() < early.mean() else 'no'})")

    _print("\n⚠️ EXPLORATORY — sign-free; only ~2 expiry days so the EXPIRY cut is anecdotal. "
           "gap→move de-trended (drift removed). Overlapping windows autocorrelate. Re-run as "
           "expiry days accrue — that's where the Ni-Pearson-Poteshman effect should show.")


if __name__ == "__main__":
    main()
