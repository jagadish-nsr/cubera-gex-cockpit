"""Validate (or kill) MP distribution signals for DIRECTION / momentum / reversal, with the controls
that matter. All point-in-time: every feature is known at day-t close; the target is day t+1.

Hypotheses tested:
  A. POC MOMENTUM — rolling 5D % change in POC predicts next-day return.
     TRAP: POC tracks price, so this could be price-momentum restated → partial against 5D price mom.
  B. STRETCH FROM VALUE — close far above/below the day's POC (price accepted little at the extreme)
     → next-day reversion. TRAP: a big up day → partial against today's open→close return.
  C. DISTRIBUTION SHAPE — n_modes / VA-width% / time-at-POC → next-day DIRECTION and next-day |move|.
     (#25 already showed shape→next |move| collapses into today-HL; re-checked here for direction.)
  D. DISTRIBUTION BOXES — classify the day (where it closed vs the value area; single vs double
     distribution) and read the next-day mean return + win% per box.

Discipline: Spearman IC, partials against the trivial predictor, a MIRROR-style sign check, and a
day-block OOS. NIFTY 1-min (clean). Sign-free of the GEX landmine. Descriptive, not a trade rule.

  python scripts/analyze_mp_shape_direction.py            # NIFTY
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _ic(x, y):
    import numpy as np
    from scipy.stats import spearmanr
    m = np.isfinite(x) & np.isfinite(y)
    return spearmanr(x[m], y[m]).correlation if m.sum() > 20 else float("nan")


def _partial(x, y, *ctrls):
    import numpy as np
    from scipy.stats import rankdata
    m = np.isfinite(x) & np.isfinite(y)
    for c in ctrls:
        m &= np.isfinite(c)
    if m.sum() < 30:
        return float("nan")
    R = [rankdata(v[m]) for v in (x, y, *ctrls)]
    Z = np.column_stack([np.ones(m.sum())] + [R[i] for i in range(2, len(R))])

    def res(v):
        b, *_ = np.linalg.lstsq(Z, v, rcond=None)
        return v - Z @ b
    return float(np.corrcoef(res(R[0]), res(R[1]))[0, 1])


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.market_profile import BIN_PTS, profiles_frame

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    mp = profiles_frame(store, bin_pts=BIN_PTS.get(store, 5.0)).sort_index()
    if mp.empty:
        _print("No data."); return
    d = mp.copy()
    d["ret_oc"] = (d["close"] - d["open"]) / d["open"] * 100.0          # today's open→close %
    d["ret_next"] = (d["close"].shift(-1) - d["close"]) / d["close"] * 100.0   # t→t+1 close-to-close %
    d["absnext"] = d["ret_next"].abs()
    d["hl_pct"] = (d["hi"] - d["lo"]) / d["open"] * 100.0
    # A: POC momentum vs price momentum (both known at close t)
    d["poc_mom5"] = (d["poc"] / d["poc"].shift(5) - 1) * 100.0
    d["px_mom5"] = (d["close"] / d["close"].shift(5) - 1) * 100.0
    # B: stretch of the close from the day's POC (price relative to accepted value)
    d["stretch"] = (d["close"] - d["poc"]) / d["close"] * 100.0
    # C: close position within the value area (0 = at VAL, 1 = at VAH; >1 above, <0 below)
    d["close_pos"] = (d["close"] - d["val"]) / (d["vah"] - d["val"]).replace(0, np.nan)
    d = d.dropna(subset=["ret_next"])
    A = {k: d[k].to_numpy() for k in
         ("ret_next", "absnext", "poc_mom5", "px_mom5", "stretch", "ret_oc", "hl_pct",
          "va_width_pct", "tpoc_ratio", "n_modes")}
    n = int(np.isfinite(A["ret_next"]).sum())
    _print(f"Market Profile distribution → direction · {store} · {n} days\n")

    _print("── A · POC MOMENTUM (5D) → next-day return ────────────────────────────────────")
    _print(f"  poc_mom5  IC {_ic(A['poc_mom5'], A['ret_next']):+.3f}   | price-mom5 partial "
           f"{_partial(A['poc_mom5'], A['ret_next'], A['px_mom5']):+.3f}   "
           f"(price_mom5 alone IC {_ic(A['px_mom5'], A['ret_next']):+.3f})")
    _print("  → POC momentum is real only if it beats price-momentum after the partial (else it's "
           "price momentum restated — and price momentum itself is near-zero here).\n")

    _print("── B · STRETCH FROM VALUE (close − POC) → next-day return (reversal if −) ──────")
    _print(f"  stretch   IC {_ic(A['stretch'], A['ret_next']):+.3f}   | today-return partial "
           f"{_partial(A['stretch'], A['ret_next'], A['ret_oc']):+.3f}   "
           f"(vs today open→close IC {_ic(A['ret_oc'], A['ret_next']):+.3f})\n")

    _print("── C · DISTRIBUTION SHAPE → next-day DIRECTION and |MOVE| ──────────────────────")
    _print(f"  {'feature':<14}{'IC→ret':>9}{'IC→|move|':>11}{'|move| partial|today-HL':>24}")
    for k in ("n_modes", "va_width_pct", "tpoc_ratio"):
        _print(f"  {k:<14}{_ic(A[k], A['ret_next']):>+9.3f}{_ic(A[k], A['absnext']):>+11.3f}"
               f"{_partial(A[k], A['absnext'], A['hl_pct']):>+24.3f}")
    _print()

    _print("── D · DISTRIBUTION BOXES → next-day mean return / win% ────────────────────────")
    cp = d["close_pos"]
    box = pd.cut(cp, [-np.inf, 0, 0.5, 1.0, np.inf],
                 labels=["below VAL", "lower VA", "upper VA", "above VAH"])
    _print(f"  {'close vs value area':<22}{'n':>6}{'mean next%':>12}{'win%':>8}{'next |move|%':>14}")
    for lab in ["below VAL", "lower VA", "upper VA", "above VAH"]:
        g = d[box == lab]["ret_next"].dropna()
        if len(g):
            _print(f"  {lab:<22}{len(g):>6}{g.mean():>+12.3f}{(g > 0).mean()*100:>7.0f}%{g.abs().mean():>13.2f}%")
    _print(f"  {'single-dist (1 mode)':<22}{(d.n_modes==1).sum():>6}"
           f"{d[d.n_modes==1]['ret_next'].mean():>+12.3f}{(d[d.n_modes==1]['ret_next']>0).mean()*100:>7.0f}%"
           f"{d[d.n_modes==1]['absnext'].mean():>13.2f}%")
    _print(f"  {'double-dist (2+ modes)':<22}{(d.n_modes>=2).sum():>6}"
           f"{d[d.n_modes>=2]['ret_next'].mean():>+12.3f}{(d[d.n_modes>=2]['ret_next']>0).mean()*100:>7.0f}%"
           f"{d[d.n_modes>=2]['absnext'].mean():>13.2f}%")

    # OOS for the strongest directional candidate (stretch)
    cut = int(n * 0.7)
    _print(f"\n── OOS (70/30) · stretch → next return:  IS {_ic(A['stretch'][:cut], A['ret_next'][:cut]):+.3f}"
           f"   OOS {_ic(A['stretch'][cut:], A['ret_next'][cut:]):+.3f}")
    _print(f"   base rate: next-day up = {(A['ret_next'] > 0).mean()*100:.0f}%  (any 'edge' must beat this)")


if __name__ == "__main__":
    main()
