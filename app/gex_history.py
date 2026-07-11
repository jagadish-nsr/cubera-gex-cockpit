"""Historical daily dealer GEX from the raw bhavcopy, with per-strike IV (the vol smile).

The one signal the research phase found lands where theory predicts: |GEX| (gamma
concentration) is inversely related to forward realized vol (SqueezeMetrics; validated
sign-free on 2 yrs, see docs/findings-summary.md). This module computes a proper daily net
GEX — per-strike Black-Scholes gamma at each strike's OWN implied vol (call for K≥S; put via
put-call parity for K<S) × (callOI − putOI) [model A dealer sign] — caches the series, and
exposes a sign-free **volatility-regime** read for the dashboard.

EOD (one value per bhavcopy day). Approximate (r=q=0). Sign-free for |GEX|; the SIGN is the
unvalidated dealer-sign landmine, so the regime read uses the MAGNITUDE only.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEX_HIST_DIR = ROOT / "data" / "journal" / "gex_history"

BAND_FRAC = 0.10
MIN_DTE = 3


def _atm_iv(near, spot, T):
    from gex.bs import implied_vol_call
    ce = near[(near["opt"] == "CE") & (near["close"] > 0)]
    if ce.empty:
        return None
    atm = ce.iloc[(ce["strike"] - spot).abs().argmin()]
    iv = implied_vol_call(float(atm["close"]), spot, float(atm["strike"]), T)
    return iv if (iv and iv == iv and iv > 0) else None


def compute_day_gex(day: str, symbol: str = "NIFTY", smile: bool = True):
    """Net GEX for one day (per-strike-IV smile by default; ``smile=False`` = one ATM IV for
    all strikes, the cruder version). None if unusable."""
    from app.bhavcopy_raw import load_raw_fo_day
    from gex.bs import bs_gamma, implied_vol_call
    import pandas as pd

    df = load_raw_fo_day(day, symbol)
    if df is None:
        return None
    opt = df[df["instr"] == "IDO"]
    if opt.empty or opt["expiry"].isna().all():
        return None
    spot = float(pd.to_numeric(df["spot"], errors="coerce").dropna().iloc[0])
    d0 = date.fromisoformat(day)
    exps = sorted(opt["expiry"].dropna().unique())
    exp = next((e for e in exps if (date.fromisoformat(e) - d0).days >= MIN_DTE), None)
    if exp is None:
        return None
    T = (date.fromisoformat(exp) - d0).days / 365.0
    near = opt[opt["expiry"] == exp]
    atm_iv = _atm_iv(near, spot, T)
    if atm_iv is None:
        return None

    gex = 0.0
    for k, g in near.groupby("strike"):
        k = float(k)
        if abs(k - spot) > spot * BAND_FRAC:
            continue
        coi = float(g[g["opt"] == "CE"]["oi"].sum())
        poi = float(g[g["opt"] == "PE"]["oi"].sum())
        iv = atm_iv
        if smile:                                    # each strike's own IV (the smile)
            if k >= spot:                            # OTM/ATM call is liquid
                row = g[(g["opt"] == "CE") & (g["close"] > 0)]
                if not row.empty:
                    v = implied_vol_call(float(row.iloc[0]["close"]), spot, k, T)
                    iv = v if (v and v == v and v > 0) else atm_iv
            else:                                    # OTM put -> synthetic call via parity
                row = g[(g["opt"] == "PE") & (g["close"] > 0)]
                if not row.empty:
                    synth = float(row.iloc[0]["close"]) + spot - k    # C = P + S - K (r=q=0)
                    v = implied_vol_call(synth, spot, k, T) if synth > 0 else None
                    iv = v if (v and v == v and v > 0) else atm_iv
        gex += bs_gamma(spot, k, T, iv) * (coi - poi)
    return gex * spot * spot * 0.01


def _cache_path(symbol: str) -> Path:
    return GEX_HIST_DIR / f"{symbol}.csv"


def gex_series(symbol: str = "NIFTY", refresh: bool = True,
               max_new: int | None = None) -> dict[str, float]:
    """Cached {day: net GEX} across the raw-bhavcopy history (append-only). ``max_new`` caps
    how many uncached days are computed this call (the NEWEST ones) — the dashboard passes a
    small number so it never triggers a full 543-day rebuild on load; a build script passes
    None to fill everything."""
    from app.bhavcopy_raw import available_days
    path = _cache_path(symbol)
    cached: dict[str, float] = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    cached[r["day"]] = float(r["gex"])
                except (KeyError, ValueError, TypeError):
                    pass
    if refresh:
        todo = [d for d in available_days(symbol) if d not in cached]
        if max_new is not None:
            todo = todo[-max_new:]                    # newest uncached days only
        if todo:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if write_header:
                    w.writerow(["day", "gex"])
                for d in todo:
                    g = compute_day_gex(d, symbol)
                    if g is not None:
                        cached[d] = g
                        w.writerow([d, g])
    return dict(sorted(cached.items()))


RV_HORIZON = 10                  # forward realized-vol horizon (matches the −0.20 validation)


def vol_regime(symbol: str = "NIFTY") -> dict | None:
    """Sign-free volatility-regime read: the latest day's |GEX| vs its full-history
    distribution, PLUS the historical mean forward realized vol (annualized %) for each
    |GEX| tercile — so the read is QUANTIFIED (high |GEX| → ~X% vol vs ~Y% in low-gamma).
    Validated inverse |GEX|→RV (IC ≈ −0.20 at 10d). Returns None if too few days."""
    import numpy as np
    from app.options_eod import daily_spot

    s = gex_series(symbol, max_new=3)
    sp = daily_spot(symbol)
    days = [d for d in sorted(s) if d in sp]
    if len(days) < 40:
        return None
    absg = np.array([abs(s[d]) for d in days])
    spot = np.array([sp[d] for d in days])
    lr = np.diff(np.log(spot), prepend=np.nan)
    rv = np.array([np.nanstd(lr[i + 1:i + 1 + RV_HORIZON]) * np.sqrt(252) * 100
                   if i + 1 + RV_HORIZON <= len(lr) else np.nan for i in range(len(days))])

    q1, q2 = np.percentile(absg, [33, 66])
    cur = absg[-1]
    pct = 100.0 * float((absg <= cur).mean())

    def _mrv(mask):
        r = rv[mask & ~np.isnan(rv)]
        return float(np.mean(r)) if len(r) else float("nan")

    lo_rv, hi_rv = _mrv(absg <= q1), _mrv(absg >= q2)
    if cur >= q2:
        tag, read, exp_rv = "calm", "HIGH gamma concentration → expect CALMER / mean-reverting", hi_rv
    elif cur <= q1:
        tag, read, exp_rv = "wild", "LOW gamma concentration → expect WILDER / trending", lo_rv
    else:
        tag, read, exp_rv = "mid", "MID gamma concentration → neutral vol regime", _mrv((absg > q1) & (absg < q2))
    return {"day": days[-1], "gex": s[days[-1]], "absgex": cur, "pct": pct, "n": len(days),
            "read": read, "tag": tag, "exp_rv": exp_rv, "lo_rv": lo_rv, "hi_rv": hi_rv,
            "horizon": RV_HORIZON}
