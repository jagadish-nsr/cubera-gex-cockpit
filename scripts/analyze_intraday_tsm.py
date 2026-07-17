"""Study #20 — signed INTRADAY time-series momentum (Gao-Han-Li-Zhou, JFE 2018) on Nifty.

The cheap, futures-only (here: spot-only) directional baseline the literature pull flagged. US
finding: the FIRST half-hour return predicts the LAST half-hour return (R²~1.6%, up to ~2.6% when
combined with the prior day's last half-hour), STRONGER on high-vol / high-volume / macro-news days.
This is genuinely UNtested here — Study #13 found morning *range* predicts afternoon *range* (a VOL
persistence, not a signed return). This tests the SIGNED momentum directly.

Point-in-time clean by construction: the predictor window (09:15→09:45) is fully known before the
target window (14:45→15:15) opens — no overlap, no look-ahead. Both returns day-detrended (subtract
the cross-day median, stripping the index's rising drift — the S4-ORB lesson). We also report the
Gao et al. combination (morning + PRIOR-day last-half return) and a high-vol subsample.

  python scripts/analyze_intraday_tsm.py            # NIFTY   [store]  [cost%]

⚠️ EXPLORATORY. Signed momentum is a directional bet (NOT the dealer-sign landmine — that's GEX sign;
this is an empirical price-momentum sign we report, not assume). Overlapping-day autocorrelation is
mild here (non-overlapping windows); costs are rough. Not a trade rule until OOS + cost clear it."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intraday_features import load_features            # noqa: E402

FIRST0, FIRST1 = "09:15", "09:45"        # first 30-min window (predictor)
LAST0, LAST1 = "14:45", "15:15"          # last 30-min window (target); 15:15 = user terminal


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _spot_at(feat, stamp):
    return feat[feat["time_ist"] == stamp].set_index("day")["spot"]


def main() -> None:
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    cost = float(sys.argv[2]) if len(sys.argv) > 2 else 0.03      # ~3bps round-trip index-fut proxy
    feat = load_features(store)
    if feat is None:
        _print("No feature cache."); return
    s_f0, s_f1 = _spot_at(feat, FIRST0), _spot_at(feat, FIRST1)
    s_l0, s_l1 = _spot_at(feat, LAST0), _spot_at(feat, LAST1)
    iv0 = feat[feat["time_ist"] == FIRST1].set_index("day")["atm_iv"]
    days = sorted(set(s_f0.index) & set(s_f1.index) & set(s_l0.index) & set(s_l1.index))

    rows = []
    for day in days:
        f0, f1, l0, l1 = s_f0.get(day), s_f1.get(day), s_l0.get(day), s_l1.get(day)
        if any(v is None or not np.isfinite(v) or v <= 0 for v in (f0, f1, l0, l1)):
            continue
        rows.append({"day": day,
                     "r_first": (f1 / f0 - 1.0) * 100.0,
                     "r_last": (l1 / l0 - 1.0) * 100.0,
                     "iv": float(iv0.get(day, np.nan)) * 100})
    d = pd.DataFrame(rows).reset_index(drop=True)
    if len(d) < 120:
        _print(f"Only {len(d)} usable days."); return
    d["r_first"] -= d["r_first"].median()                         # day-detrend both windows
    d["r_last"] -= d["r_last"].median()
    d["prev_last"] = d["r_last"].shift(1)                          # prior day's last-half (Gao combo)

    _print(f"Signed intraday momentum  ·  {store}  ·  {len(d)} days ({d['day'].iloc[0]} … "
           f"{d['day'].iloc[-1]})  ·  first {FIRST0}–{FIRST1} → last {LAST0}–{LAST1}\n")
    ic = d["r_first"].corr(d["r_last"], method="spearman")
    pear = d["r_first"].corr(d["r_last"])
    sign = (np.sign(d["r_first"]) == np.sign(d["r_last"])).mean() * 100
    _print(f"  Spearman(r_first, r_last) = {ic:+.3f}   Pearson = {pear:+.3f}   "
           f"sign-agreement {sign:.1f}%  (50% = chance)\n")

    _print("Mean day-detrended LAST-half return by FIRST-half tercile (momentum ⇒ monotone up):")
    q1, q2 = d["r_first"].quantile([1 / 3, 2 / 3])
    for lab, sub in (("DOWN (low r_first)", d[d["r_first"] <= q1]),
                     ("FLAT (mid)", d[(d["r_first"] > q1) & (d["r_first"] < q2)]),
                     ("UP (high r_first)", d[d["r_first"] >= q2])):
        _print(f"  {lab:<20} r_last mean {sub['r_last'].mean():+.4f}%   (n {len(sub)})")

    # tradeable: at 14:45 take the SIGN of the morning move, hold to 15:15, net of cost
    dirn = np.sign(d["r_first"])
    pnl = dirn * d["r_last"] - cost
    _print(f"\nTradeable (enter 14:45 in morning's direction, exit 15:15, cost {cost:.2f}%):")
    _print(f"  mean net {pnl.mean():+.4f}%   win {100*(pnl>0).mean():.1f}%   median {pnl.median():+.4f}%")

    # Gao combo: trade only when morning AND prior-day-last agree
    both = d.dropna(subset=["prev_last"])
    agree = both[np.sign(both["r_first"]) == np.sign(both["prev_last"])]
    if len(agree) > 40:
        pnl2 = np.sign(agree["r_first"]) * agree["r_last"] - cost
        _print(f"  Gao combo (morning & prior-last agree, n {len(agree)}): net {pnl2.mean():+.4f}%  "
               f"win {100*(pnl2>0).mean():.1f}%")

    # high-vol subsample (Gao: momentum concentrates on high-vol days)
    hi = d[d["iv"] >= d["iv"].median()]
    ic_hi = hi["r_first"].corr(hi["r_last"], method="spearman")
    _print(f"  High-IV half (n {len(hi)}): Spearman {ic_hi:+.3f}  "
           f"tradeable net {(np.sign(hi['r_first'])*hi['r_last']-cost).mean():+.4f}%")

    # walk-forward OOS: the sign rule has no fitted params, so just report test-half P&L
    cut = d["day"].iloc[int(len(d) * 0.70)]
    te = d[d["day"] >= cut]
    pnl_te = np.sign(te["r_first"]) * te["r_last"] - cost
    _print(f"\nWalk-forward OOS (test ≥{cut}, n {len(te)}): net {pnl_te.mean():+.4f}%  "
           f"win {100*(pnl_te>0).mean():.1f}%")
    _print("\n  Real edge only if IC>0 clearly, tercile monotone, tradeable net > 0 after cost, and "
           "OOS agrees. Else intraday momentum is dead too (only the VOL persistence, #13, survives).")
    _print("⚠️ Signed directional bet; day-detrended; rough cost; sign-free of the GEX landmine.")


if __name__ == "__main__":
    main()
