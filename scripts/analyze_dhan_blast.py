"""Gamma-blast re-validation at real n: does the TRUE-vs-FAKE blast_score predict continuation?

The blast staying-power grade (app/gammablast.py::blast_score) was validated on only n=24 live
events (Spearman +0.79). Every other small-n hint in this project decayed toward zero as n grew,
so this re-runs it on the 5yr Dhan store (1-min per-strike CE/PE premium + OI).

Per day, for each FIXED near-money strike K, we track (CE_ltp − PE_ltp) on the 5-min grid — by
parity ≈ spot − K, so a sign change = spot crossing K (a candidate blast). At each crossing we
score it with blast_score(direction, crossed-strike series, wall-ahead series) over the next
HORIZON, and measure the REALIZED continuation = (signed spot move past K over the same window).
If the score is real, higher score → more continuation (positive Spearman); expiry-day (low DTE)
events should be the cleanest.

  python scripts/analyze_dhan_blast.py            # NIFTY   [store]  [max_dte]

⚠️ Exploratory; overlapping/selection; the score's calibration was set on 2 events. Sign-free."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GRID_MIN = 5
_OPEN = 9 * 60 + 15
HORIZON = 8                       # 8 x 5-min = 40-min total window (matches the live watch)
OBS = 3                          # score is computed over the FIRST OBS stamps (15 min) only;
                                 # continuation is measured AFTER that → a point-in-time test
NEAR = 3                         # only strikes within ±3 steps of spot at the crossing (near-money)


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _grid(df):
    def mins(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    return [t for t in sorted(df["time_ist"].unique(), key=mins) if (mins(t) - _OPEN) % GRID_MIN == 0]


def _day_events(store, day, dte):
    import numpy as np
    from data.dhan import load_intraday_day
    from app.gammablast import blast_score

    df = load_intraday_day(store, day)
    if df is None or df.empty:
        return []
    stamps = _grid(df)
    if len(stamps) < HORIZON + 4:
        return []
    sidx = {t: i for i, t in enumerate(stamps)}
    n = len(stamps)
    spot = np.full(n, np.nan)
    # per (strike, side): close & oi arrays over the grid
    ce_l, pe_l, ce_o, pe_o = {}, {}, {}, {}
    for _, r in df[df["time_ist"].isin(sidx)].iterrows():
        i = sidx[r["time_ist"]]
        spot[i] = r["spot"]
        k = float(r["strike"])
        if r["side"] == "CE":
            ce_l.setdefault(k, [np.nan] * n)[i] = r["close"]
            ce_o.setdefault(k, [np.nan] * n)[i] = r["oi"]
        else:
            pe_l.setdefault(k, [np.nan] * n)[i] = r["close"]
            pe_o.setdefault(k, [np.nan] * n)[i] = r["oi"]
    strikes = sorted(set(ce_l) & set(pe_l))
    if len(strikes) < 3:
        return []
    step = min((b - a) for a, b in zip(strikes, strikes[1:]))
    events = []
    for K in strikes:
        cl, pl = ce_l[K], pe_l[K]
        co, po = ce_o[K], pe_o[K]
        diff = [(cl[i] - pl[i]) if (cl[i] == cl[i] and pl[i] == pl[i]) else np.nan for i in range(n)]
        prev = None
        for i in range(n):
            d = diff[i]
            if d != d:
                continue
            side = 1 if d > 0 else -1 if d < 0 else 0
            if side == 0:
                prev = (i, side)
                continue
            if prev is not None and side != prev[1] and i + HORIZON < n:
                pi = prev[0]
                if abs(K - spot[i]) > NEAR * step:            # near-money crossings only
                    prev = (i, side)
                    continue
                direction = "up" if side > 0 else "down"      # diff -→+ = spot crossed up through K
                seg = range(i, i + OBS + 1)                     # OBSERVATION window only (point-in-time)
                crossed = [{"call_oi": co[j], "put_oi": po[j],
                            "call_ltp": cl[j], "put_ltp": pl[j]} for j in seg]
                Kah = K + step if direction == "up" else K - step
                ahead = None
                if Kah in ce_o:
                    ahead = [{"call_oi": ce_o[Kah][j], "put_oi": pe_o.get(Kah, [np.nan] * n)[j],
                              "call_ltp": ce_l[Kah][j], "put_ltp": pe_l.get(Kah, [np.nan] * n)[j]}
                             for j in seg]
                bs = blast_score(direction, crossed, ahead)
                if bs is None or spot[i + OBS] != spot[i + OBS] or spot[i + HORIZON] != spot[i + HORIZON]:
                    prev = (i, side)
                    continue
                dirsign = 1 if direction == "up" else -1
                # outcome measured AFTER the observation window; momentum = the obs-window move
                cont = dirsign * (spot[i + HORIZON] - spot[i + OBS])
                obs_move = dirsign * (spot[i + OBS] - spot[i])
                events.append({"day": day, "K": K, "score": bs["score"], "grade": bs["grade"],
                               "cont": cont, "obs_move": obs_move, "dte": dte})
            prev = (i, side)
    return events


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    max_dte = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
    feat = load_features(store)
    if feat is None:
        _print("No feature cache — need it for per-day DTE.")
        return
    dte_by_day = feat.groupby("day")["dte"].first().to_dict()
    days = [d for d, v in dte_by_day.items() if v <= max_dte]      # near-expiry (blast context)
    _print(f"Gamma-blast re-validation  ·  {store}  ·  {len(days)} near-expiry days (DTE≤{max_dte})\n")
    rows = []
    for di, d in enumerate(sorted(days), 1):
        rows.extend(_day_events(store, d, dte_by_day[d]))
        if di % 100 == 0:
            _print(f"  …{di}/{len(days)} days, {len(rows)} events")
    if len(rows) < 30:
        _print(f"Only {len(rows)} events.")
        return
    df = pd.DataFrame(rows)
    ic = df["score"].corr(df["cont"], method="spearman")
    ic_mom = df["obs_move"].corr(df["cont"], method="spearman")      # momentum benchmark
    part = _partial(df["score"], df["cont"], df["obs_move"])          # score beyond momentum
    _print(f"\nPOINT-IN-TIME test — score from first {OBS*GRID_MIN}min, continuation over the "
           f"remaining {(HORIZON-OBS)*GRID_MIN}min  ·  {len(df)} events\n")
    _print(f"  Spearman(blast_score , later continuation)        = {ic:+.3f}")
    _print(f"  Spearman(obs-window move , later continuation)     = {ic_mom:+.3f}   (momentum benchmark)")
    _print(f"  Partial(blast_score , continuation | obs-move)     = {part:+.3f}   (score BEYOND momentum)\n")
    _print("Mean LATER continuation (pts) by score bucket:")
    df["bucket"] = pd.cut(df["score"], [-1, 20, 35, 50, 65, 101],
                          labels=["0–20", "20–35", "35–50", "50–65", "65–100"])
    for b, g in df.groupby("bucket", observed=True):
        _print(f"  score {b:>7}: n={len(g):>5}  mean {g['cont'].mean():+6.1f}  "
               f"median {g['cont'].median():+6.1f}  %continued {100*(g['cont']>0).mean():4.0f}%")
    _print(f"\n  Contemporaneous (full-window) IC was +0.64; this splits observation from outcome. "
           "If the PARTIAL (vs momentum) collapses to ~0, the score is confirmation, not prediction.")
    _print("  ⚠️ Exploratory; overlapping/selection; calibration set on 2 events. Sign-free.")


def _partial(x, y, z):
    """Spearman(x, y | z): rank-residual correlation after removing rank(z)."""
    import numpy as np
    import pandas as pd
    rx, ry, rz = (pd.Series(v).rank().to_numpy() for v in (x, y, z))
    A = np.c_[np.ones_like(rz), rz]
    ex = rx - A @ np.linalg.lstsq(A, rx, rcond=None)[0]
    ey = ry - A @ np.linalg.lstsq(A, ry, rcond=None)[0]
    return float(pd.Series(ex).corr(pd.Series(ey)))


if __name__ == "__main__":
    main()
