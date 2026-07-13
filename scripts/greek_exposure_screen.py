"""Screen dealer-greek exposures (beyond GEX) for point-in-time predictive value.

GEX = gamma×OI is a vol/regime metric (no direction). This screens the OTHER dealer-greek
exposures — especially the ones with a DIRECTIONAL mechanism GEX lacks:
  DEX   = Σ (Δc·callOI + Δp·putOI)             net dealer delta tilt (directional)
  DFLOW = Σ (Δc·callVol + Δp·putVol)           delta-weighted VOLUME flow (aggressive, directional)
  DDOI  = Σ (Δc·ΔcallOI + Δp·ΔputOI)           delta-weighted FRESH positioning (ΔOI, directional)
  CHARM = Σ charm·(callOI − putOI)             delta-decay flow → expiry drift (directional)
  VANNA = Σ vanna·(callOI − putOI)             vol↔spot feedback (directional)
  VEGA  = Σ vega·(callOI + putOI)              vol exposure (magnitude)
  GEX   = Σ gamma·(callOI − putOI)             reference (vol/regime)
All from per-strike OTM-smile IV (r=q=0), ATM±10. Directional metrics inherit the dealer-sign
landmine, so we screen BOTH signed IC (direction) and |IC|; magnitude metrics are sign-free.

  python scripts/greek_exposure_screen.py            # NIFTY  [stride]  [max_dte]

Point-in-time IC of each exposure (and its Δ over 15min) vs forward 30-min RETURN (direction) and
forward 30-min VOL (size). ⚠️ Exploratory; overlapping windows; screen only, not a trade rule."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GRID = 5
_OPEN = 9 * 60 + 15
H = 6                             # 30-min forward horizon (6×5min)
BPY = 252 * 75
METRICS = ["DEX", "DFLOW", "DDOI", "CHARM", "VANNA", "VEGA", "GEX"]


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
    return [t for t in sorted(df["time_ist"].unique(), key=mins) if (mins(t) - _OPEN) % GRID == 0]


def _day_metrics(store, day, T):
    import numpy as np
    from scipy.stats import norm
    from data.dhan import load_intraday_day
    if not (T and T > 0):
        return None
    df = load_intraday_day(store, day)
    if df is None or df.empty:
        return None
    df = df.copy()
    df["iv"] = df["iv"] / 100.0
    stamps = _grid(df)
    prev_oi = {}                                        # (strike,side) -> oi at previous stamp
    rows = []
    sq = np.sqrt(T)
    for st in stamps:
        s = df[df["time_ist"] == st]
        spot = float(s["spot"].iloc[0])
        ce = s[s["side"] == "CE"].groupby("strike").agg(oi=("oi", "sum"), iv=("iv", "mean"), v=("volume", "sum"))
        pe = s[s["side"] == "PE"].groupby("strike").agg(oi=("oi", "sum"), iv=("iv", "mean"), v=("volume", "sum"))
        K = np.array(sorted(set(ce.index) | set(pe.index)), float)
        if len(K) < 5:
            continue
        coi = ce["oi"].reindex(K).to_numpy(); poi = pe["oi"].reindex(K).to_numpy()
        cv = ce["v"].reindex(K).to_numpy(); pv = pe["v"].reindex(K).to_numpy()
        civ = ce["iv"].reindex(K).to_numpy(); piv = pe["iv"].reindex(K).to_numpy()
        iv = np.where(K >= spot, civ, piv)              # OTM-side smile
        iv = np.where(np.isfinite(iv) & (iv > 0), iv, np.nan)
        coi = np.nan_to_num(coi); poi = np.nan_to_num(poi)
        cv = np.nan_to_num(cv); pv = np.nan_to_num(pv)
        # ΔOI since previous stamp
        dcoi = np.array([coi[i] - prev_oi.get((K[i], "C"), coi[i]) for i in range(len(K))])
        dpoi = np.array([poi[i] - prev_oi.get((K[i], "P"), poi[i]) for i in range(len(K))])
        for i in range(len(K)):
            prev_oi[(K[i], "C")] = coi[i]; prev_oi[(K[i], "P")] = poi[i]
        good = np.isfinite(iv)
        if good.sum() < 5:
            continue
        Kk, ivk = K[good], iv[good]
        d1 = (np.log(spot / Kk) + 0.5 * ivk**2 * T) / (ivk * sq)
        d2 = d1 - ivk * sq
        phi = norm.pdf(d1)
        dc = norm.cdf(d1); dp = dc - 1.0               # call/put delta
        vega = spot * phi * sq
        vanna = -phi * d2 / ivk
        charm = phi * d2 / (2.0 * T)                    # r=q=0 delta-decay
        gamma = phi / (spot * ivk * sq)
        cO, pO = coi[good], poi[good]
        cV, pV = cv[good], pv[good]
        dcO, dpO = dcoi[good], dpoi[good]
        rows.append({
            "day": day, "time_ist": st, "spot": spot,
            "DEX": float(np.sum(dc * cO + dp * pO)),
            "DFLOW": float(np.sum(dc * cV + dp * pV)),
            "DDOI": float(np.sum(dc * dcO + dp * dpO)),
            "CHARM": float(np.sum(charm * (cO - pO))),
            "VANNA": float(np.sum(vanna * (cO - pO))),
            "VEGA": float(np.sum(vega * (cO + pO))),
            "GEX": float(np.sum(gamma * (cO - pO)) * spot * spot * 0.01),
        })
    return rows


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    stride = int(sys.argv[2]) if len(sys.argv) > 2 else 4     # sample every Nth day (speed)
    max_dte = float(sys.argv[3]) if len(sys.argv) > 3 else 99
    feat = load_features(store)
    dte_by_day = feat.groupby("day")["dte"].first().to_dict()
    days = [d for d in sorted(dte_by_day) if dte_by_day[d] <= max_dte][::stride]
    _print(f"Greek-exposure screen · {store} · {len(days)} days (stride {stride}, DTE≤{max_dte})\n")
    rows = []
    for i, d in enumerate(days, 1):
        r = _day_metrics(store, d, dte_by_day[d] / 365.0)
        if r:
            rows.extend(r)
        if i % 100 == 0:
            _print(f"  …{i}/{len(days)} days")
    df = pd.DataFrame(rows)
    # forward return (direction) + forward vol (size), within day, point-in-time
    parts = []
    for _, g in df.groupby("day"):
        g = g.sort_values("time_ist").copy()
        sp = g["spot"].to_numpy()
        lr = np.clip(np.diff(np.log(sp), prepend=np.nan), -0.08, 0.08)
        fr = np.full(len(g), np.nan); fv = np.full(len(g), np.nan)
        for j in range(len(g) - H):
            fr[j] = (sp[j + H] / sp[j] - 1) * 100
            w = lr[j + 1:j + 1 + H]
            if np.isfinite(w).sum() >= 2:
                fv[j] = np.nanstd(w) * np.sqrt(BPY) * 100
        g["fret"] = fr; g["fvol"] = fv
        for m in METRICS:                               # Δ over 15 min (3 stamps)
            g[m + "_d"] = g[m] - g[m].shift(3)
        parts.append(g)
    df = pd.concat(parts, ignore_index=True)
    # merge ATM IV (to control the "|metric| ∝ 1/σ is just IV" confound) + DTE
    iv = feat[["day", "time_ist", "atm_iv", "dte"]]
    df = df.merge(iv, on=["day", "time_ist"], how="left")

    def _partial(x, y, z):                              # Spearman(x,y|z)
        d = pd.DataFrame({"x": x, "y": y, "z": z}).dropna()
        if len(d) < 500:
            return float("nan")
        r = d.rank()
        A = np.c_[np.ones(len(d)), r["z"]]
        ex = r["x"] - A @ np.linalg.lstsq(A, r["x"], rcond=None)[0]
        ey = r["y"] - A @ np.linalg.lstsq(A, r["y"], rcond=None)[0]
        return float(pd.Series(ex).corr(pd.Series(ey)))

    _print(f"\n{len(df):,} stamp-rows. Point-in-time IC (Spearman):\n")
    _print(f"  {'metric':<8} {'signed→ret':>11} {'|·|→fvol':>10} {'|·|→fvol|IV':>12} "
           f"{'|·|→fvol|DTE':>13} {'Δ15→ret':>9}")
    for m in METRICS:
        d = df[[m, m + "_d", "fret", "fvol", "atm_iv", "dte"]].dropna()
        if len(d) < 500:
            continue
        sret = d[m].corr(d["fret"], method="spearman")
        absvol = d[m].abs().corr(d["fvol"], method="spearman")
        piv = _partial(d[m].abs(), d["fvol"], d["atm_iv"])
        pdte = _partial(d[m].abs(), d["fvol"], d["dte"])
        dret = d[m + "_d"].corr(d["fret"], method="spearman")
        _print(f"  {m:<8} {sret:>+11.3f} {absvol:>+10.3f} {piv:>+12.3f} {pdte:>+13.3f} {dret:>+9.3f}")
    _print("\n  signed→ret  : predict forward RETURN direction? (sign-dependent)  Δ15→ret: flow.")
    _print("  |·|→fvol    : magnitude predicts realized vol.  |IV / |DTE = partial, controlling")
    _print("               ATM IV / DTE — the real test (|VANNA|∝1/σ, |VEGA|∝√T are IV/DTE in disguise).")
    _print("  A vol-metric BEATS GEX only if its |·|→fvol|IV partial exceeds GEX's. ⚠️ Screen only.")


if __name__ == "__main__":
    main()
