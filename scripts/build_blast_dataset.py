"""Build a leak-free ML dataset for TRUE-gamma-blast prediction from the Dhan intraday store.

Each row = one near-money strike crossing (spot passing a fixed K, by parity CE_ltp≈PE_ltp).
FEATURES are computed only over the OBS-stamp observation window [i, i+OBS] and prior session
(point-in-time — you'd enter at i+OBS, "close to the start"). The TARGET is the continuation
AFTER that, over [i+OBS, i+H] — so features never see the outcome. We store the signed
continuation %, plus max favourable/adverse excursion (for realistic P&L / EV eval later).

  python scripts/build_blast_dataset.py            # NIFTY  [max_dte]

Writes data/journal/blast_ml/<store>.parquet (gitignored). Sign-free; decision-support research."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GRID_MIN = 5
_OPEN = 9 * 60 + 15
OBS = 2                           # observation window = 2×5min = 10 min (enter near the start)
HORIZON = 8                       # outcome measured to i+8 (40 min from the cross)
NEAR = 3                          # crossings within ±3 steps of spot only
OUT = ROOT / "data" / "journal" / "blast_ml"


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


def _frac(a, b):
    return (b - a) / a if (a and a == a and b == b) else 0.0


def fut_map(store, day, stamps):
    """Align the 1-min near-month futures (OHLC+vol+OI) to the option 5-min grid: per stamp,
    {oi, close, vol5=volume in the 5 min ending at the stamp}. None if no futures for the day."""
    import numpy as np
    from data.dhan import load_futures_intraday_day
    fdf = load_futures_intraday_day(store, day)
    if fdf is None or fdf.empty:
        return None

    def mins(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    fm = np.array([mins(t) for t in fdf["time_ist"]])
    order = np.argsort(fm)
    fm, foi, fcl, fv = fm[order], fdf["oi"].to_numpy()[order], fdf["close"].to_numpy()[order], fdf["volume"].to_numpy()[order]
    out = {}
    for t in stamps:
        M = mins(t)
        j = np.searchsorted(fm, M, side="right") - 1          # last futures bar ≤ stamp
        if j < 0:
            continue
        w = (fm > M - GRID_MIN) & (fm <= M)
        out[t] = {"oi": float(foi[j]), "close": float(fcl[j]), "vol5": float(np.nansum(fv[w]))}
    return out or None


def _day_rows(store, day, dte, gex_map, futm=None):
    import numpy as np
    from scipy.stats import norm
    from data.dhan import load_intraday_day

    T = (dte / 365.0) if (dte and dte > 0) else None
    df = load_intraday_day(store, day)
    if df is None or df.empty:
        return []
    stamps = _grid(df)
    if len(stamps) < HORIZON + OBS + 3:
        return []
    sidx = {t: i for i, t in enumerate(stamps)}
    n = len(stamps)
    spot = np.full(n, np.nan)
    L = {}                                              # per strike: arrays for both sides
    keys = ("cl", "pl", "co", "po", "civ", "piv", "cv", "pv")
    for _, r in df[df["time_ist"].isin(sidx)].iterrows():
        i = sidx[r["time_ist"]]
        spot[i] = r["spot"]
        k = float(r["strike"])
        d = L.setdefault(k, {kk: [np.nan] * n for kk in keys})
        if r["side"] == "CE":
            d["cl"][i], d["co"][i], d["civ"][i], d["cv"][i] = r["close"], r["oi"], r["iv"], r["volume"]
        else:
            d["pl"][i], d["po"][i], d["piv"][i], d["pv"][i] = r["close"], r["oi"], r["iv"], r["volume"]
    strikes = sorted(k for k in L if any(x == x for x in L[k]["cl"]) and any(x == x for x in L[k]["pl"]))
    if len(strikes) < 5:
        return []
    step = min((b - a) for a, b in zip(strikes, strikes[1:]))
    # session realized vol up to each stamp (context)
    lr = np.clip(np.diff(np.log(np.where(spot > 0, spot, np.nan)), prepend=np.nan), -0.08, 0.08)

    sqT = (T ** 0.5) if T else None

    def charm_ex(j, sref):
        """Signed dealer-charm exposure Σ charm·(callOI−putOI) over ATM±NEAR at stamp j (raw iv
        is percent → /100). Directional flow that drives expiry drift — the candidate blast-
        direction signal."""
        if not T:
            return np.nan
        tot = 0.0
        for Kk in strikes:
            if abs(Kk - sref) > NEAR * step:
                continue
            dd = L[Kk]
            coi = dd["co"][j] if dd["co"][j] == dd["co"][j] else 0.0
            poi = dd["po"][j] if dd["po"][j] == dd["po"][j] else 0.0
            iv = (dd["civ"][j] if Kk >= sref else dd["piv"][j])
            if not (iv == iv and iv > 0):
                continue
            iv = iv / 100.0
            d1 = (np.log(sref / Kk) + 0.5 * iv * iv * T) / (iv * sqT)
            d2 = d1 - iv * sqT
            tot += (norm.pdf(d1) * d2 / (2.0 * T)) * (coi - poi)
        return tot

    rows = []
    for K in strikes:
        d = L[K]
        diff = [(d["cl"][i] - d["pl"][i]) if (d["cl"][i] == d["cl"][i] and d["pl"][i] == d["pl"][i])
                else np.nan for i in range(n)]
        prev = None
        for i in range(n):
            v = diff[i]
            if v != v:
                continue
            side = 1 if v > 0 else -1 if v < 0 else 0
            if side == 0:
                prev = side
                continue
            if prev is not None and side != prev and OBS <= i <= n - HORIZON - 1:
                if abs(K - spot[i]) > NEAR * step:
                    prev = side
                    continue
                io = i + OBS
                if spot[io] != spot[io] or spot[i + HORIZON] != spot[i + HORIZON] or spot[i] != spot[i]:
                    prev = side
                    continue
                up = side > 0
                dirs = 1.0 if up else -1.0
                itm_l = d["cl"] if up else d["pl"]           # ITM-going side premium
                itm_o = d["co"] if up else d["po"]           # ITM-going side OI
                itm_v = d["cv"] if up else d["pv"]
                otm_iv = d["piv"] if up else d["civ"]        # OTM side IV (liquid) at K
                Kah = K + step if up else K - step
                dah = L.get(Kah)
                # --- features over [i, io] (point-in-time) ---
                cover = _frac(itm_o[i], itm_o[io])           # <0 = writers covering = fuel
                thrust = _frac(itm_l[i], itm_l[io])          # ITM premium expansion
                wall_ratio = wall_erode = 0.0
                if dah is not None:
                    aoi = dah["co"] if up else dah["po"]
                    if aoi[io] == aoi[io] and itm_o[io]:
                        wall_ratio = aoi[io] / itm_o[io] if itm_o[io] else 0.0
                    wall_erode = _frac(aoi[i], aoi[io])
                mom = dirs * (spot[io] - spot[i]) / spot[i]
                ivn = otm_iv[io] if otm_iv[io] == otm_iv[io] else np.nan
                div = _frac(otm_iv[i], otm_iv[io]) if (otm_iv[i] and otm_iv[i] == otm_iv[i]) else 0.0
                vol_obs = np.nansum(itm_v[i:io + 1])
                turnover = vol_obs / itm_o[io] if itm_o[io] else 0.0
                # skew ≈ put_iv(below) − call_iv(above) 2 steps out
                kd, ku = K - 2 * step, K + 2 * step
                skew = 0.0
                if L.get(kd) and L.get(ku):
                    pv = L[kd]["piv"][io]
                    cv2 = L[ku]["civ"][io]
                    if pv == pv and cv2 == cv2:
                        skew = pv - cv2
                # near PCR over K±3
                near_k = [K + j * step for j in range(-3, 4)]
                tc = np.nansum([L[k2]["co"][io] for k2 in near_k if L.get(k2)])
                tp = np.nansum([L[k2]["po"][io] for k2 in near_k if L.get(k2)])
                pcr = tp / tc if tc else np.nan
                rv_sofar = np.nanstd(lr[1:i + 1]) * np.sqrt(252 * 75) * 100 if i > 3 else np.nan
                absg = gex_map.get((day, stamps[io]), np.nan)
                # --- intraday FUTURES features over [i, io] (only where futures data exists) ---
                fut_doi = fut_mom = fut_to = np.nan
                if futm is not None:
                    a, b = futm.get(stamps[i]), futm.get(stamps[io])
                    if a and b and a["oi"]:
                        fut_doi = (b["oi"] - a["oi"]) / a["oi"]                 # futures OI build-up %
                        fut_mom = dirs * (b["close"] / a["close"] - 1) * 100 if a["close"] else np.nan
                        vobs = sum(futm[stamps[j]]["vol5"] for j in range(i + 1, io + 1)
                                   if stamps[j] in futm)
                        fut_to = vobs / b["oi"] if b["oi"] else np.nan          # futures turnover
                # --- target over [io, i+HORIZON] (no lookahead) ---
                fut = spot[io + 1:i + HORIZON + 1]
                cont = dirs * (spot[i + HORIZON] - spot[io]) / spot[io] * 100
                exc = dirs * (fut - spot[io]) / spot[io] * 100
                mfe = float(np.nanmax(exc)) if len(exc) else 0.0
                mae = float(np.nanmin(exc)) if len(exc) else 0.0
                # winning (ITM-going) leg premium MULTIPLE over the payoff window [io, i+HORIZON]
                # — the retail "2–3× in minutes" claim. entry = at io (point-in-time entry).
                entry_prem = itm_l[io]
                win_leg = itm_l[io:i + HORIZON + 1]
                have_entry = entry_prem == entry_prem and entry_prem > 0
                blast_mult_exit = (itm_l[i + HORIZON] / entry_prem) if have_entry else np.nan
                blast_mult_peak = (float(np.nanmax(win_leg)) / entry_prem
                                   if (have_entry and len(win_leg)) else np.nan)
                charm = charm_ex(io, spot[io])                              # signed dealer-charm
                charm_flow = (charm - charm_ex(i, spot[i])) if T else np.nan
                rows.append({
                    "day": day, "K": K, "dir": 1 if up else 0, "dte": dte,
                    "tod": (_OPEN + i * GRID_MIN),
                    # features
                    "cover": cover, "thrust": thrust, "wall_ratio": wall_ratio,
                    "wall_erode": wall_erode, "mom_obs": mom * 100, "iv": ivn, "div_obs": div,
                    "skew": skew, "turnover": turnover, "pcr": pcr, "rv_sofar": rv_sofar,
                    "absg": absg, "dist_step": (K - spot[i]) / step,
                    "charm": charm, "charm_flow": charm_flow,
                    # targets
                    "cont": cont, "mfe": mfe, "mae": mae,
                    # straddle at the crossed strike K: entry (at io) & exit (at i+HORIZON) premiums
                    "strad_entry": (d["cl"][io] + d["pl"][io]),
                    "strad_exit": (d["cl"][i + HORIZON] + d["pl"][i + HORIZON]),
                    # winning-leg premium multiple (retail 2–3× claim): entry premium + exit/peak ×
                    "blast_entry_prem": entry_prem, "blast_mult_exit": blast_mult_exit,
                    "blast_mult_peak": blast_mult_peak,
                    # intraday futures (NaN where no futures data for the day)
                    "fut_doi": fut_doi, "fut_mom": fut_mom, "fut_to": fut_to,
                })
            prev = side
    return rows


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    max_dte = float(sys.argv[2]) if len(sys.argv) > 2 else 2.5
    feat = load_features(store)
    if feat is None:
        _print("No feature cache.")
        return
    dte_by_day = feat.groupby("day")["dte"].first().to_dict()
    gex_map = {(r.day, r.time_ist): abs(r.net_gex) for r in feat.itertuples()}
    days = sorted(d for d, v in dte_by_day.items() if v <= max_dte)
    _print(f"Building blast ML dataset · {store} · {len(days)} near-expiry days (DTE≤{max_dte}), "
           f"OBS={OBS*GRID_MIN}min entry, outcome to {HORIZON*GRID_MIN}min\n")
    rows = []
    for di, d in enumerate(days, 1):
        rows.extend(_day_rows(store, d, dte_by_day[d], gex_map))
        if di % 100 == 0:
            _print(f"  …{di}/{len(days)} days, {len(rows)} crossings")
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT / f"{store}.parquet", index=False)
    _print(f"\n✅ {len(df)} crossings → {OUT / (store + '.parquet')}")
    _print(f"   base rate (cont>0): {100*(df['cont']>0).mean():.1f}%   "
           f"mean cont {df['cont'].mean():+.3f}%   median {df['cont'].median():+.3f}%")
    _print(f"   'big true blast' (cont>0.15%): {100*(df['cont']>0.15).mean():.1f}%")


if __name__ == "__main__":
    main()
