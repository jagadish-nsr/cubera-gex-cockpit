"""Does the FII index-futures positioning predict the NEXT DAY's Nifty return?

Point-in-time test: the FII feature is known at day t's CLOSE (EOD participant file),
the target is the Nifty close-to-close return from t to the next trading day. Two
features: the LEVEL (net long−short futures) and the daily DRIFT (Δ level vs the prior
captured day). Reports the Spearman rank IC (robust to outliers) with n and the aligned
pairs so you can eyeball it.

  python scripts/analyze_participant_ic.py

⚠️ Exploratory, decision-support only. With a handful of captured days this is WILDLY
underpowered — read the sign/shape, never the magnitude, until n grows. Nifty daily
"close" = the last clean in-session spot in the time-series journal (a proxy, not the
official settlement)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.magnet_trend import _clean_day  # noqa: E402
from app.participant_oi import available_days as poi_days  # noqa: E402
from app.participant_oi import load_participant_day  # noqa: E402
from app.timeseries import available_days as ts_days  # noqa: E402
from app.timeseries import load_day  # noqa: E402

_MIN_ROWS = 6  # a day needs this many clean in-session snapshots to give a real close


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def nifty_daily_close(symbol: str = "NIFTY") -> dict[str, float]:
    """{date_ist: close} from the time-series journal — the last clean in-session
    spot each day (frozen/partial days with < _MIN_ROWS snapshots are dropped)."""
    out: dict[str, float] = {}
    for d in ts_days(symbol):
        clean = _clean_day(load_day(d, symbol))
        if clean is None or clean.empty or len(clean) < _MIN_ROWS:
            continue
        sp = pd.to_numeric(clean["spot"], errors="coerce").dropna()
        if len(sp):
            out[d] = float(sp.iloc[-1])
    return out


def fii_net_fut() -> dict[str, float]:
    """{date_ist: FII net index futures (long − short)} from the participant journal."""
    out: dict[str, float] = {}
    for d in poi_days():
        p = load_participant_day(d)
        f = (p or {}).get("FII")
        if f and f.get("fut_long") is not None and f.get("fut_short") is not None:
            out[d] = float(f["fut_long"] - f["fut_short"])
    return out


def build_pairs() -> pd.DataFrame:
    """One row per FII day that has both a same-day close and a next-trading-day close:
    date, fii_level, fii_drift (vs prior FII day), fwd_ret (close_t -> close_next), and
    the calendar gap to that next close (1 = a true next-day; >1 spans a data gap)."""
    closes = nifty_daily_close()
    fii = fii_net_fut()
    close_days = sorted(closes)
    fii_dates = sorted(fii)

    rows = []
    for i, d in enumerate(fii_dates):
        if d not in closes:
            continue
        nexts = [c for c in close_days if c > d]
        if not nexts:
            continue
        nd = nexts[0]
        drift = fii[d] - fii[fii_dates[i - 1]] if i > 0 else None
        gap = (pd.Timestamp(nd) - pd.Timestamp(d)).days
        rows.append({"date": d, "next": nd, "gap_days": gap,
                     "fii_level": fii[d], "fii_drift": drift,
                     "fwd_ret_pct": (closes[nd] / closes[d] - 1.0) * 100.0})
    return pd.DataFrame(rows)


def main() -> None:
    df = build_pairs()
    _print("FII futures positioning  ->  next-day Nifty return  (point-in-time)\n")
    if df.empty:
        _print("No aligned pairs yet — need overlapping participant-OI and time-series "
               "days. Backfill/accrue more, then re-run.")
        return

    show = df.copy()
    show["fii_level"] = show["fii_level"].map(lambda x: f"{x:+,.0f}")
    show["fii_drift"] = show["fii_drift"].map(lambda x: f"{x:+,.0f}" if pd.notna(x) else "—")
    show["fwd_ret_pct"] = show["fwd_ret_pct"].map(lambda x: f"{x:+.2f}%")
    _print(show.to_string(index=False))

    def _ic(col, label):
        sub = df[[col, "fwd_ret_pct"]].dropna()
        n = len(sub)
        if n < 3:
            _print(f"\n{label}: n={n} — too few pairs for an IC.")
            return
        ic = sub[col].corr(sub["fwd_ret_pct"], method="spearman")
        _print(f"\n{label}: Spearman IC = {ic:+.2f}  (n={n})")

    _ic("fii_level", "FII net-futures LEVEL vs next-day return")
    _ic("fii_drift", "FII net-futures DRIFT (Δ vs prior day) vs next-day return")

    ngap = int((df["gap_days"] > 1).sum())
    _print(f"\n⚠️ EXPLORATORY — n is tiny; read sign/shape only, not magnitude. "
           f"{ngap} pair(s) span a data gap (gap_days>1) so 'next-day' is approximate. "
           "Close = last clean in-session spot (proxy). A negative IC would mean FII "
           "shorts are CONTRARIAN (fade them); positive = FII lead. Do NOT trade this "
           "until n grows and the sign holds out-of-sample.")


if __name__ == "__main__":
    main()
