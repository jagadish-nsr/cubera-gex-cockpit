"""Do our FORWARD-PRICED option levels (magnet / max-pain / call & put walls) predict where price
later ACCEPTS — the future TPO POC / VAH / VAL — across timeframes (1D → 1W → 2W → 1M)?

The hunch: options are forward-priced, so the magnet/max-pain should carry information about
tomorrow's (and next week's) point of control, and the walls about the value-area edges.

THE TRAP (why a naive test lies): magnet ≈ max-pain ≈ POC ≈ today's close, all clustered near spot,
and tomorrow's POC ≈ today's close (small daily moves). So "magnet predicts POC" is trivially true.
Everything is tested in DEVIATIONS FROM TODAY'S CLOSE, and every option level must beat two naive
anchors: (a) close itself (offset 0 → error = |future−close|) and (b) TODAY's TPO level (persistence).
An option level only has forward value if it correlates with the future level's offset AND lowers the
error / adds partial correlation beyond today's TPO.

Point-in-time: EOD option levels of day t (known at t's close) predict the TPO of t+1 … t+H (strictly
after). 1-min spot (NIFTY clean). Sign-free, descriptive. Decision-support, not a trade rule.

  python scripts/analyze_mp_vs_options.py            # NIFTY
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HORIZONS = [("1D", 1), ("1W", 5), ("2W", 10), ("1M", 20)]


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _corr(x, y):
    import numpy as np
    from scipy.stats import spearmanr
    m = np.isfinite(x) & np.isfinite(y)
    return spearmanr(x[m], y[m]).correlation if m.sum() > 20 else float("nan")


def _partial(x, y, z):
    """Spearman partial corr of x,y given z (does the option level add beyond today's TPO level)."""
    import numpy as np
    from scipy.stats import rankdata
    m = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    if m.sum() < 30:
        return float("nan")
    rx, ry, rz = rankdata(x[m]), rankdata(y[m]), rankdata(z[m])
    Z = np.column_stack([np.ones(m.sum()), rz])

    def res(v):
        b, *_ = np.linalg.lstsq(Z, v, rcond=None)
        return v - Z @ b
    return float(np.corrcoef(res(rx), res(ry))[0, 1])


def _mae(pred_off, fwd_off):
    import numpy as np
    m = np.isfinite(pred_off) & np.isfinite(fwd_off)
    return float(np.mean(np.abs(pred_off[m] - fwd_off[m]))) if m.any() else float("nan")


def main() -> None:
    import numpy as np
    import pandas as pd
    from data.dhan import available_intraday_days
    from app.market_profile import BIN_PTS, day_profile, spot_path
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    bp = BIN_PTS.get(store, 5.0)
    _print(f"Option levels → future TPO levels · {store} · bin {bp:g}pt · deviations-from-close\n")

    # per-day: 1-min spot array + own TPO + close
    spot, daily = {}, {}
    for d in available_intraday_days(store):
        sp = spot_path(store, d)
        if sp is None:
            continue
        pr = day_profile(sp, bp)
        if pr:
            spot[d] = sp.to_numpy()
            pr["close"] = float(sp.iloc[-1])
            daily[d] = pr
    days = sorted(daily)

    # EOD option levels (last stamp of each day) — known at that day's close
    feat = load_features(store).sort_values(["day", "time_ist"])
    eod = feat.groupby("day").last()[["call_wall", "put_wall", "magnet", "max_pain", "gamma_flip"]]

    def composite(dlist):
        arrs = [spot[d] for d in dlist if d in spot]
        if len(arrs) < 1:
            return None
        return day_profile(pd.Series(np.concatenate(arrs)), bp)

    for lbl, H in HORIZONS:
        rows = []
        for i in range(len(days) - H):
            d = days[i]
            comp = composite(days[i + 1:i + 1 + H])          # forward composite TPO (t+1 … t+H)
            if not comp or d not in eod.index:
                continue
            c = daily[d]["close"]
            e = eod.loc[d]
            rows.append({
                "poc_f": comp["poc"] - c, "vah_f": comp["vah"] - c, "val_f": comp["val"] - c,
                "poc_now": daily[d]["poc"] - c, "vah_now": daily[d]["vah"] - c, "val_now": daily[d]["val"] - c,
                "magnet": e["magnet"] - c, "maxpain": e["max_pain"] - c,
                "cwall": e["call_wall"] - c, "pwall": e["put_wall"] - c,
            })
        R = pd.DataFrame(rows).dropna()
        A = {k: R[k].to_numpy() for k in R.columns}
        _print(f"── {lbl} ({H}d forward, n={len(R)}) ── corr(offset, future-offset) + error (pts) ──")
        _print(f"  target = POC(fwd):   baseline anchors →  close(err {_mae(A['poc_f']*0, A['poc_f']):.0f})  "
               f"todayPOC(corr {_corr(A['poc_now'], A['poc_f']):+.2f}, err {_mae(A['poc_now'], A['poc_f']):.0f})")
        for name, key in (("magnet", "magnet"), ("max_pain", "maxpain")):
            c_raw = _corr(A[key], A["poc_f"])
            c_par = _partial(A[key], A["poc_f"], A["poc_now"])       # beyond today's POC
            _print(f"      {name:<9} corr {c_raw:+.2f}  | todayPOC-partial {c_par:+.2f}  | err {_mae(A[key], A['poc_f']):.0f}")
        _print(f"  target = VAH(fwd):   call_wall corr {_corr(A['cwall'], A['vah_f']):+.2f}  "
               f"partial|todayVAH {_partial(A['cwall'], A['vah_f'], A['vah_now']):+.2f}  "
               f"err {_mae(A['cwall'], A['vah_f']):.0f}  (vs todayVAH err {_mae(A['vah_now'], A['vah_f']):.0f})")
        _print(f"  target = VAL(fwd):   put_wall  corr {_corr(A['pwall'], A['val_f']):+.2f}  "
               f"partial|todayVAL {_partial(A['pwall'], A['val_f'], A['val_now']):+.2f}  "
               f"err {_mae(A['pwall'], A['val_f']):.0f}  (vs todayVAL err {_mae(A['val_now'], A['val_f']):.0f})\n")

    _print("Read: an option level has FORWARD value only if its corr with the future offset is real AND "
           "its partial beyond today's TPO stays non-zero AND its error beats the naive anchors (close, "
           "today's TPO). A level whose only 'edge' is being near spot = the trivial-anchor false positive.")


if __name__ == "__main__":
    main()
