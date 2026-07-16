"""Revalidate SUPPORT / RESISTANCE — the sign-free OI-build footprint vs the dealer-sign GEX walls.

The dashboard offers two support/resistance reads:
  • GEX walls (call_wall / put_wall) — ride the UNVALIDATED India dealer-sign convention (CLAUDE.md).
  • Footprint OI-build (support_build = put-ΔOI-weighted strike; resistance_build = call-ΔOI-weighted)
    — SIGN-FREE (put-build = support, call-build = resistance is a heuristic, but immune to the
    dealer-gamma-sign landmine). "Fresh writing near spot" is exactly what the panel shows.

Question: does price actually RESPECT these levels (bounce at support, reject at resistance) — and
does the sign-free footprint do so better than the dealer-sign walls, and better than a placebo?

Method (mirrors analyze_wall_break, sign-free): the footprint S/R is computed per stamp from the raw
1-min Dhan per-strike OI as the ADDS-only, band-limited, ΔOI-weighted strike (OI change since the
day's open — same build_footprint logic, current expiry). "Near a level" = gap ≤ the horizon's
expected move (σ√τ from ATM IV). Signed PUSH toward the level over a day-detrended forward return:
  near resistance (from below) → push = +fwd   (up = toward/through resistance)
  near support   (from above)  → push = −fwd   (down = toward/through support)
push < 0 ⇒ the level REJECTS (holds); push > 0 ⇒ break/continue. A MID-ZONE placebo (near neither)
is the non-level baseline. Split by expiry-structure era (app/expiry_regime). Point-in-time.

  python scripts/analyze_footprint_sr.py            # NIFTY  [max_days]  [band_frac=0.03]

⚠️ Exploratory; overlapping windows autocorrelate; sign-free; footprint S/R needs the raw store."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIN_PER_YEAR = 252 * 375
GRID = 5
HORIZ = 30                     # primary forward horizon (min) for the era table
_OPEN = 9 * 60 + 15


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _mins(t: str) -> int:
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


# the support/resistance flavours we compute per stamp (column suffixes)
_FLAVOURS = ["dw", "dt", "ow", "ot"]   # ΔOI-weighted, ΔOI-top, OI-weighted, OI-top


def _day_footprint(store, day, band_frac):
    """{time_ist: {sup_<f>, res_<f>}} at 5-min stamps from the raw 1-min store, for four S/R
    flavours near spot (±band): ΔOI-WEIGHTED centre-of-mass, ΔOI-TOP single peak strike, total-
    OI-WEIGHTED, total-OI-TOP. Support = put side, resistance = call side. ΔOI = OI since open;
    current expiry (Dhan rollingoption is the near expiry). Mirrors build_footprint (adds-only,
    banded) for the weighted centre; 'top' is the single most-built / most-held strike."""
    import numpy as np
    from data.dhan import load_intraday_day
    df = load_intraday_day(store, day)
    if df is None or getattr(df, "empty", True):
        return {}
    ce = df[df["side"] == "CE"].drop_duplicates(["time_ist", "strike"]).pivot(
        index="time_ist", columns="strike", values="oi")
    pe = df[df["side"] == "PE"].drop_duplicates(["time_ist", "strike"]).pivot(
        index="time_ist", columns="strike", values="oi")
    spot = df.groupby("time_ist")["spot"].first()
    pe = pe.reindex(columns=ce.columns)               # align put strikes to the call grid
    times = sorted(set(ce.index) & set(pe.index), key=_mins)   # only stamps present on BOTH sides
    if not times:
        return {}
    ks = ce.columns.to_numpy(float)
    ce0 = np.nan_to_num(ce.loc[times[0]].to_numpy(float))
    pe0 = np.nan_to_num(pe.loc[times[0]].to_numpy(float))
    out = {}
    for t in times:
        if (_mins(t) - _OPEN) % GRID != 0:
            continue
        s = spot.get(t)
        if s is None or s != s:
            continue
        cur_c = np.nan_to_num(ce.loc[t].to_numpy(float))
        cur_p = np.nan_to_num(pe.loc[t].to_numpy(float))
        band = np.abs(ks - float(s)) <= float(s) * band_frac
        if not band.any():
            continue
        kk = ks[band]
        ca = np.clip(cur_c[band] - ce0[band], 0, None)   # call ADDS (resistance, ΔOI)
        pa = np.clip(cur_p[band] - pe0[band], 0, None)   # put ADDS  (support, ΔOI)
        coi = cur_c[band]                                # total call OI (standing resistance)
        poi = cur_p[band]                                # total put OI  (standing support)

        def _w(w):
            return float((kk * w).sum() / w.sum()) if w.sum() > 0 else np.nan

        def _top(w):
            return float(kk[int(np.argmax(w))]) if (w > 0).any() else np.nan
        out[t] = {"sup_dw": _w(pa), "res_dw": _w(ca), "sup_dt": _top(pa), "res_dt": _top(ca),
                  "sup_ow": _w(poi), "res_ow": _w(coi), "sup_ot": _top(poi), "res_ot": _top(coi)}
    return out


def _prep(store, max_days, band_frac):
    import numpy as np
    import pandas as pd
    from app.expiry_regime import expiry_era
    from app.intraday_features import load_features
    f = load_features(store)
    if f is None or "call_wall" not in f.columns:
        _print("No levels-enriched feature cache — rebuild it first.")
        return None
    f = f.sort_values(["day", "time_ist"]).reset_index(drop=True)
    days = sorted(f["day"].unique())
    if max_days:
        days = days[-max_days:]                    # most-recent N (keeps the current regime)
        f = f[f["day"].isin(days)].reset_index(drop=True)

    # footprint S/R (4 flavours) per (day, time_ist) from the raw store
    fp_map = {}
    for di, d in enumerate(days, 1):
        for t, vals in _day_footprint(store, d, band_frac).items():
            fp_map[(d, t)] = vals
        if di % 100 == 0:
            _print(f"  …footprint {di}/{len(days)} days")
    key = list(zip(f["day"], f["time_ist"]))
    for fl in _FLAVOURS:
        f[f"sup_{fl}"] = [fp_map.get(k, {}).get(f"sup_{fl}", np.nan) for k in key]
        f[f"res_{fl}"] = [fp_map.get(k, {}).get(f"res_{fl}", np.nan) for k in key]

    # day-detrended forward return at HORIZ, and expected-move unit
    m = HORIZ // GRID
    parts = []
    for _, g in f.groupby("day"):
        g = g.copy()
        sp = g["spot"].to_numpy()
        fwd = np.full(len(g), np.nan)
        for i in range(len(g) - m):
            fwd[i] = np.clip(sp[i + m] / sp[i] - 1.0, -0.08, 0.08)
        g["fwd"] = (fwd - np.nanmean(fwd)) if np.isfinite(fwd).any() else fwd
        # spot at t+m (same day) for the cross-rate
        fut = np.full(len(g), np.nan)
        for i in range(len(g) - m):
            fut[i] = sp[i + m]
        g["fut"] = fut
        parts.append(g)
    f = pd.concat(parts, ignore_index=True)
    f["em"] = f["spot"] * f["atm_iv"] / np.sqrt(MIN_PER_YEAR) * np.sqrt(HORIZ)
    f["era"] = f["day"].map(expiry_era)
    return f


def _levelset(f, scol, rcol):
    """(push, near_mask, cross, mid_mask) for a support/resistance column pair."""
    import numpy as np
    gap_res = f[rcol] - f["spot"]
    gap_sup = f["spot"] - f[scol]
    near_res = (gap_res > 0) & (gap_res <= f["em"])
    near_sup = (gap_sup > 0) & (gap_sup <= f["em"])
    push = np.where(near_res, f["fwd"], np.where(near_sup, -f["fwd"], np.nan))
    near = near_res | near_sup
    mid = (~near) & (gap_res > f["em"]) & (gap_sup > f["em"])
    # crossed the level within the horizon?
    crossed = np.where(near_res, f["fut"] > f[rcol],
                       np.where(near_sup, f["fut"] < f[scol], np.nan)).astype(float)
    return push, near.to_numpy(), crossed, mid.to_numpy()


def _report(f, scol, rcol, label):
    import numpy as np
    import pandas as pd
    from app.expiry_regime import ERA_LABELS, era_order
    push, near, crossed, mid = _levelset(f, scol, rcol)
    d = pd.DataFrame({"push": push, "cross": crossed, "era": f["era"].to_numpy(),
                      "mid_push": np.where(mid, f["fwd"], np.nan)})
    _print(f"\n{label} — PUSH toward the level @{HORIZ}m (bp; <0 = REJECTS/holds, >0 = breaks) + cross-rate:")
    _print(f"  {'era':<42} {'near-level':>10} {'placebo':>9} {'edge':>7} {'cross%':>7} {'n':>6}")
    for e in era_order(d["era"].unique()):
        g = d[d["era"] == e]
        w = g[np.isfinite(g["push"])]
        if len(w) < 200:
            _print(f"  {ERA_LABELS[e]:<42} (n={len(w)} thin)"); continue
        nl = w["push"].mean() * 1e4
        pl = g["mid_push"].mean() * 1e4
        cr = 100.0 * np.nanmean(w["cross"])
        _print(f"  {ERA_LABELS[e]:<42} {nl:>+9.1f} {pl:>+8.1f} {nl-pl:>+6.1f} {cr:>6.1f}% {len(w):>6,}")
    w = d[np.isfinite(d["push"])]
    _print(f"  {'ALL eras':<42} {w['push'].mean()*1e4:>+9.1f} {d['mid_push'].mean()*1e4:>+8.1f} "
           f"{(w['push'].mean()-d['mid_push'].mean())*1e4:>+6.1f} {100*np.nanmean(w['cross']):>6.1f}% {len(w):>6,}")


def main() -> None:
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    max_days = int(sys.argv[2]) if len(sys.argv) > 2 else 0        # 0 = all days
    band_frac = float(sys.argv[3]) if len(sys.argv) > 3 else 0.03
    f = _prep(store, max_days, band_frac)
    if f is None:
        return
    _print(f"\nSupport/Resistance revalidation · {store} · {f['day'].nunique()} days · "
           f"band ±{band_frac*100:.0f}% · horizon {HORIZ}m · WEIGHTED (centre-of-mass) vs TOP (peak strike)")
    _report(f, "sup_dw", "res_dw", "(A) Footprint ΔOI-WEIGHTED  (fresh build · centre-of-mass · sign-free)")
    _report(f, "sup_dt", "res_dt", "(B) Footprint ΔOI-TOP       (fresh build · single peak strike · sign-free)")
    _report(f, "sup_ow", "res_ow", "(C) Total-OI-WEIGHTED       (standing book · centre-of-mass · sign-free)")
    _report(f, "sup_ot", "res_ot", "(D) Total-OI-TOP            (standing book · single peak strike · sign-free)")
    _report(f, "put_wall", "call_wall", "(E) GEX walls             (gamma/OI peak · engine · dealer-sign)")
    _print("\n  Read: a level that WORKS shows near-level push clearly NEGATIVE and BELOW the placebo "
           "(negative 'edge') — price rejects there more than at a random spot. Compare WEIGHTED (A/C) vs "
           "TOP (B/D) and fresh-ΔOI (A/B) vs standing-OI (C/D) vs gamma (E): a TOP single strike may hold "
           "where a blended centre-of-mass doesn't. If every edge ≈ 0 / placebo, none contains price at "
           "this horizon. ⚠️ sign-free; overlapping windows; era-split.")


if __name__ == "__main__":
    main()
