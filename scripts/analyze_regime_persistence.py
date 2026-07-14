"""Regime persistence / transition — is today's vol-regime label sticky into TOMORROW, and does it
carry forward-looking info beyond today's realized vol?  [sharpens how much to trust the gauge]

Three questions, all point-in-time (today known at close, tomorrow is the next trading session; pairs
only across gaps ≤4 days so we never bridge a data hole):

  A. REALIZED-VOL clustering — bucket each day by its realized day-HL range (terciles). Does a
     quiet/wild day predict a quiet/wild tomorrow?  Transition matrix + autocorr + run length. This is
     the clean, DTE-free core (day-HL has no mechanical expiry-cycle).
  B. Does the |vanna| GAUGE label add beyond today's realized vol?  Median tomorrow day-HL by today's
     EOD |vanna| tercile, and partial(today_vanna, tomorrow_HL | today_HL [+ DTE]) — if ~0 the gauge
     is just a same-day vol read, not a next-day forecast.
  C. Label STICKINESS — P(tomorrow same |vanna| tercile).  ⚠️ |vanna| has a weekly DTE cycle (rises
     toward expiry) so raw stickiness is inflated by that cycle; we also report it with |vanna| ranked
     WITHIN a DTE bin to strip the cycle out.

Day-HL uses the 5-min cache spot (cache-only, fast); |vanna| EOD = |vanna| at the last stamp (the
value you'd have at the close to position for tomorrow). Sign-free.

  python scripts/analyze_regime_persistence.py            # NIFTY   [store]

⚠️ Descriptive; 5-min-sampled range; sign-free."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_MAX_DAY_HL = 12.0        # >12% intraday range = corrupt Dhan tick


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _bucket(v, q1, q2):
    import numpy as np
    return np.where(v <= q1, 0, np.where(v >= q2, 2, 1))     # 0=LOW/TRENDY 1=MID 2=HIGH/SIDEWAYS


def _partial(x, y, ctrls):
    import numpy as np, pandas as pd
    d = pd.DataFrame({"x": x, "y": y})
    for i, c in enumerate(ctrls):
        d[f"z{i}"] = c
    d = d.dropna()
    if len(d) < 40:
        return float("nan")
    r = d.rank()
    A = np.c_[np.ones(len(d)), *[r[f"z{i}"] for i in range(len(ctrls))]]
    ex = r["x"] - A @ np.linalg.lstsq(A, r["x"], rcond=None)[0]
    ey = r["y"] - A @ np.linalg.lstsq(A, r["y"], rcond=None)[0]
    return float(pd.Series(ex).corr(pd.Series(ey), method="spearman"))


def _runlen(seq, target):
    """Mean consecutive-run length of `target` in the bucket sequence."""
    runs, cur = [], 0
    for x in seq:
        if x == target:
            cur += 1
        elif cur:
            runs.append(cur); cur = 0
    if cur:
        runs.append(cur)
    return sum(runs) / len(runs) if runs else 0.0


def main() -> None:
    import numpy as np, pandas as pd
    from app.intraday_features import load_features
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    f = load_features(store)
    if f is None:
        _print("No feature cache.")
        return
    f = f.sort_values(["day", "time_ist"])
    g = f.groupby("day")
    d = pd.DataFrame({
        "vanna_med": g["vanna"].agg(lambda s: s.abs().median()),
        "vanna_eod": g["vanna"].last().abs(),
        "dte": g["dte"].first(),
        "open": g["spot"].first(),
        "hi": g["spot"].max(), "lo": g["spot"].min(),
    }).reset_index()
    d["day_hl"] = (d["hi"] - d["lo"]) / d["open"] * 100.0
    d = d[(d["day_hl"] > 0) & (d["day_hl"] <= _MAX_DAY_HL)].copy()
    d["date"] = pd.to_datetime(d["day"])
    d = d.sort_values("date").reset_index(drop=True)

    # pair each day with the next trading session (gap ≤ 4 calendar days)
    d["gap"] = d["date"].diff().dt.days.shift(-1)
    nxt = d.shift(-1)
    P = pd.DataFrame({
        "t_hl": d["day_hl"], "n_hl": nxt["day_hl"],
        "t_van": d["vanna_eod"], "n_van": nxt["vanna_eod"],
        "t_dte": d["dte"], "gap": d["gap"],
    }).dropna()
    P = P[P["gap"] <= 4]
    _print(f"Regime persistence · {store} · {len(P)} adjacent-day pairs "
           f"(of {len(d)} days)\n")

    # ---- A: realized-vol regime clustering (DTE-free) -----------------------------------------
    q1, q2 = d["day_hl"].quantile([1 / 3, 2 / 3])
    tb, nb = _bucket(P["t_hl"].values, q1, q2), _bucket(P["n_hl"].values, q1, q2)
    ac = P["t_hl"].corr(P["n_hl"], method="spearman")
    same = 100.0 * np.mean(tb == nb)
    _print("(A) REALIZED-VOL regime (day-HL terciles) — quiet/wild day clustering:")
    _print(f"  Spearman(today_HL, tomorrow_HL) = {ac:+.3f}   ·   P(tomorrow SAME tercile) = "
           f"{same:.0f}%  (chance 33%)")
    lbls = ["LOW/TRENDY", "MID", "HIGH/SIDEWAYS"]
    _print(f"  Transition  P(tomorrow | today):   {'tom→':>8}" +
           "".join(f"{l:>15}" for l in lbls))
    for i, tl in enumerate(lbls):
        row = nb[tb == i]
        cnt = [100.0 * np.mean(row == j) for j in range(3)] if len(row) else [float("nan")] * 3
        _print(f"    today {tl:>13}  " + "".join(f"{c:>14.0f}%" for c in cnt) + f"   (n={len(row)})")
    seq = _bucket(d["day_hl"].values, q1, q2)
    _print(f"  mean run length: TRENDY {_runlen(seq,0):.1f}d · SIDEWAYS {_runlen(seq,2):.1f}d "
           f"(persistent if > ~1.5d)\n")

    # ---- B: does the |vanna| gauge add beyond today's realized vol? ---------------------------
    vq1, vq2 = d["vanna_eod"].quantile([1 / 3, 2 / 3])
    P = P.assign(tvb=_bucket(P["t_van"].values, vq1, vq2))
    _print("(B) Does today's |vanna| gauge label forecast tomorrow's MOVE beyond today's vol?")
    _print("  median tomorrow day-HL% by TODAY's |vanna|(eod) tercile:")
    for j, lbl in [(2, "HIGH → SIDEWAYS"), (1, "MID"), (0, "LOW → TRENDY")]:
        _print(f"    {lbl:>16}: {P[P['tvb']==j]['n_hl'].median():.2f}%")
    ic = P["t_van"].corr(P["n_hl"], method="spearman")
    p_hl = _partial(P["t_van"], P["n_hl"], [P["t_hl"]])
    p_hld = _partial(P["t_van"], P["n_hl"], [P["t_hl"], P["t_dte"]])
    _print(f"  Spearman(today_vanna, tomorrow_HL) = {ic:+.3f} · partial|today_HL = {p_hl:+.3f} · "
           f"partial|today_HL+DTE = {p_hld:+.3f}")
    _print("  (partial ~0 ⇒ the gauge is a SAME-day vol read, not a next-day forecast beyond "
           "realized vol)\n")

    # ---- C: label stickiness (raw vs DTE-stripped) --------------------------------------------
    tvb, nvb = _bucket(P["t_van"].values, vq1, vq2), _bucket(P["n_van"].values, vq1, vq2)
    same_raw = 100.0 * np.mean(tvb == nvb)
    # DTE-strip: rank |vanna| within a DTE bin (rounded), bucket the residual rank
    d["dteb"] = d["dte"].round()
    d["van_resid"] = d.groupby("dteb")["vanna_eod"].rank(pct=True)
    rr = d.set_index("day")["van_resid"]
    Pr = pd.DataFrame({"t": d["van_resid"].values, "n": d["van_resid"].shift(-1).values,
                       "gap": d["gap"].values}).dropna()
    Pr = Pr[Pr["gap"] <= 4]
    tb2 = _bucket(Pr["t"].values, 1 / 3, 2 / 3)
    nb2 = _bucket(Pr["n"].values, 1 / 3, 2 / 3)
    same_dte = 100.0 * np.mean(tb2 == nb2)
    _print("(C) Dashboard |vanna|-label STICKINESS — P(tomorrow same tercile):")
    _print(f"  raw = {same_raw:.0f}%   ·   DTE-stripped (rank within DTE bin) = {same_dte:.0f}%   "
           f"(chance 33%)")
    _print("  (raw > DTE-stripped ⇒ much of the apparent stickiness is just the weekly vanna cycle)")

    _print("\n  Takeaway: realized-vol regime (A) should cluster (vol clustering); the useful question "
           "is whether the gauge adds a NEXT-DAY forecast beyond that (B partial) and how much of the "
           "label's day-to-day stickiness is real vs the DTE cycle (C). Sign-free, descriptive.")


if __name__ == "__main__":
    main()
