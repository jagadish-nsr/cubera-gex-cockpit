"""Scenario I — Opening-Range Breakout (ORB) × pin regime.  [docs/scenario-catalog.md]

The intraday-price analogue of B1/B2: the Opening Range (OR) high/low form price S/R; does a breakout
RUN (trend) or FAIL (revert), and does the pin/concentration tercile flip it? Then size the run vs the
Study #15 per-bucket day range. Sign-free (tested in the break's own direction).

OR window W ∈ {15,30,60} min from 09:15 → OR_high/OR_low/OR_range (1-min spot). Breakout = first 1-min
close beyond a boundary by ≥ buffer = max(0.10·OR_range, 3bp of open). Outcomes after breakout→close:
  mfe_r = max favorable excursion beyond the boundary / OR_range   (how many OR-ranges it ran)
  mae_r = max adverse (back inside) / OR_range
  TRUE run = mfe_r ≥ 1.0 ;  FAKE = day closes back INSIDE the OR ;  close_beyond = closes past boundary
Pin tercile = dealer concentration AT the OR-window close (point-in-time), |vanna| off-expiry /
|charm| ≤1.5 DTE (the gauge switch): HIGH pin=SIDEWAYS … LOW pin=TRENDY.

Controls: vol-partial (does pin add beyond atm_iv?), a mid-day placebo range (is the OPENING range
special?), and a last-30% walk-forward split on the primary (30-min TRUE-run rate by pin tercile).

  python scripts/analyze_orb.py            # NIFTY   [store]

⚠️ Exploratory; 1-min spot; sign-free; robust (median/rates). Requires the feature cache."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OPEN = 9 * 60 + 15
WINDOWS = [15, 30, 60]
PLACEBO_START, PLACEBO_W = 11 * 60, 30      # a mid-day same-duration range (opening-specialness test)


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _stamp(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _day_spot(store, day):
    import numpy as np
    from data.dhan import load_intraday_day
    df = load_intraday_day(store, day)
    if df is None or df.empty:
        return None
    sp = df.groupby("time_ist")["spot"].first()
    mins = np.array([int(t[:2]) * 60 + int(t[3:]) for t in sp.index])
    order = np.argsort(mins)
    return mins[order], sp.to_numpy()[order]


def _orb(mins, val, start, W):
    """OR breakout outcomes for the window [start, start+W]. None if unusable."""
    import numpy as np
    orm = (mins >= start) & (mins <= start + W)
    post = mins > start + W
    if orm.sum() < 8 or post.sum() < 10:
        return None
    orh, orl = float(val[orm].max()), float(val[orm].min())
    orng = orh - orl
    op = float(val[mins <= start + W][0]) if (mins <= start + W).any() else float(val[0])
    if orng <= 0 or op <= 0:
        return None
    buf = max(0.10 * orng, 3e-4 * op)
    pv = val[post]
    day_hl_pct = (float(val.max()) - float(val.min())) / op * 100.0
    up, dn = pv > orh + buf, pv < orl - buf
    bo = np.where(up | dn)[0]
    res = {"or_range": orng, "or_range_pct": orng / op * 100.0, "open": op,
           "broke": bool(len(bo)), "day_hl_pct": day_hl_pct}
    if not len(bo):
        return res
    i = int(bo[0])
    d = 1 if up[i] else -1
    seg = pv[i:]
    boundary = orh if d == 1 else orl
    close = float(val[-1])
    if d == 1:
        mfe, mae, cb = seg.max() - boundary, max(0.0, boundary - seg.min()), close > orh
    else:
        mfe, mae, cb = boundary - seg.min(), max(0.0, seg.max() - boundary), close < orl
    res.update({"dir": d, "mfe_r": mfe / orng, "mae_r": mae / orng, "close_beyond": bool(cb),
                "fake": bool(orl <= close <= orh), "true_run": bool(mfe / orng >= 1.0)})
    return res


def _partial(x, y, z):
    import numpy as np, pandas as pd
    d = pd.DataFrame({"x": x, "y": y, "z": z}).dropna()
    if len(d) < 30:
        return float("nan")
    r = d.rank()
    A = np.c_[np.ones(len(d)), r["z"]]
    ex = r["x"] - A @ np.linalg.lstsq(A, r["x"], rcond=None)[0]
    ey = r["y"] - A @ np.linalg.lstsq(A, r["y"], rcond=None)[0]
    return float(pd.Series(ex).corr(pd.Series(ey), method="spearman"))


def _build(store):
    import numpy as np, pandas as pd
    from data.dhan import available_intraday_days
    from app.intraday_features import load_features
    feat = load_features(store)
    if feat is None:
        return None, None
    conc_at = {W: feat[feat["time_ist"] == _stamp(OPEN + W)].set_index("day") for W in WINDOWS}
    rows, plc = [], []
    for day in available_intraday_days(store):
        s = _day_spot(store, day)
        if s is None:
            continue
        mins, val = s
        for W in WINDOWS:
            o = _orb(mins, val, OPEN, W)
            if o is None or o["day_hl_pct"] > 12:
                continue
            c = conc_at[W]
            if day not in c.index:
                continue
            r = c.loc[day]
            dte = float(r["dte"])
            metric = "charm" if dte <= 1.5 else "vanna"
            iv = float(r["atm_iv"]) if r["atm_iv"] == r["atm_iv"] else np.nan
            rows.append({**o, "W": W, "day": day, "conc": abs(float(r[metric])), "atm_iv": iv,
                         "dte": dte, "expiry": dte <= 1.5})
        p = _orb(mins, val, PLACEBO_START, PLACEBO_W)
        if p is not None and p["day_hl_pct"] <= 12:
            plc.append({**p, "day": day})
    return pd.DataFrame(rows), pd.DataFrame(plc)


def _pin_bucket(sub, col="conc"):
    q1, q2 = sub[col].quantile([1 / 3, 2 / 3])
    import numpy as np
    return np.where(sub[col] >= q2, "HIGH (SIDEWAYS)",
                    np.where(sub[col] <= q1, "LOW (TRENDY)", "MID"))


def main() -> None:
    import numpy as np, pandas as pd
    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    df, plc = _build(store)
    if df is None or df.empty:
        _print("No data / feature cache.")
        return
    ne = df[~df["expiry"]].copy()
    _print(f"I · ORB × pin · {store} · {df['day'].nunique()} days "
           f"(non-expiry {ne['day'].nunique()}) · buffer=max(0.10·OR,3bp)\n")

    # ---- I1: breakout follow-through by pin tercile × OR window (NON-EXPIRY) -------------------
    _print("(I1) NON-EXPIRY — break incidence & follow-through by pin tercile "
           "[TRUE run = ran ≥1×OR_range]:")
    _print(f"  {'W':>4} {'pin bucket':>16} | {'n':>5} {'broke%':>7} | {'TRUE-run%':>9} {'fake%':>6} "
           f"{'close-bey%':>10} {'med mfe_r':>9}")
    for W in WINDOWS:
        sub = ne[ne["W"] == W].copy()
        sub["pin"] = _pin_bucket(sub)
        for pin in ["HIGH (SIDEWAYS)", "MID", "LOW (TRENDY)"]:
            g = sub[sub["pin"] == pin]
            b = g[g["broke"]]
            star = "  ←primary" if W == 30 and pin in ("HIGH (SIDEWAYS)", "LOW (TRENDY)") else ""
            tr = 100 * b["true_run"].mean() if len(b) else float("nan")
            fk = 100 * b["fake"].mean() if len(b) else float("nan")
            cb = 100 * b["close_beyond"].mean() if len(b) else float("nan")
            _print(f"  {W:>4} {pin:>16} | {len(g):>5} {100*g['broke'].mean():>6.0f}% | "
                   f"{tr:>8.0f}% {fk:>5.0f}% {cb:>9.0f}% {b['mfe_r'].median():>9.2f}{star}")
        _print()

    # ---- I2: range-hold (no breakout) share by pin, 30-min ------------------------------------
    s30 = ne[ne["W"] == 30].copy()
    s30["pin"] = _pin_bucket(s30)
    _print("(I2) 30-min OR — range-HOLD share (day never breaks the OR by the buffer), by pin:")
    for pin in ["HIGH (SIDEWAYS)", "MID", "LOW (TRENDY)"]:
        g = s30[s30["pin"] == pin]
        _print(f"    {pin:>16}: {100*(~g['broke']).mean():>4.0f}% hold   (n={len(g)})")

    # ---- I3: run vs day-range linkage (30-min, breakout days) ---------------------------------
    _print("\n(I3) 30-min OR breakout days — run size & the day it produced, by pin "
           "(Study #15 day-HL baseline in []):")
    base = {"HIGH (SIDEWAYS)": 0.61, "MID": 0.81, "LOW (TRENDY)": 1.14}  # NIFTY non-exp Study #15
    _print(f"    {'pin':>16} | {'med mfe_r':>9} {'med day-HL%':>11} {'[#15 base]':>10}")
    for pin in ["HIGH (SIDEWAYS)", "MID", "LOW (TRENDY)"]:
        b = s30[(s30["pin"] == pin) & (s30["broke"])]
        _print(f"    {pin:>16} | {b['mfe_r'].median():>9.2f} {b['day_hl_pct'].median():>10.2f}% "
               f"{base.get(pin, float('nan')):>9.2f}%")
    bo = s30[s30["broke"]]
    rex = bo["or_range_pct"].corr(bo["mfe_r"], method="spearman")
    _print(f"    range-EXHAUSTION ρ(OR_range%, mfe_r) = {rex:+.3f}  (negative = wide OR → less room)")

    # ---- I4: OR-WIDTH bins (H-L)/H — 'smaller OR → trendier?' (user hypothesis) ----------------
    _print("\n(I4) 30-min NON-EXPIRY by OR-WIDTH tercile (H−L)/H — the 'smaller OR → trendier' test.")
    _print("     ⚠️ mfe_r is OR-NORMALIZED so it rises MECHANICALLY as OR shrinks; mfe%(abs) and "
           "day-HL% are ABSOLUTE — the real coiled-spring must show in THOSE.")
    sw = ne[ne["W"] == 30].copy()
    q1w, q2w = sw["or_range_pct"].quantile([1 / 3, 2 / 3])
    sw["wbin"] = np.where(sw["or_range_pct"] <= q1w, "SMALL OR",
                          np.where(sw["or_range_pct"] >= q2w, "WIDE OR", "MID OR"))
    _print(f"    {'OR-width':>9} {'medOR%':>7} | {'broke%':>7} {'TRUE-run%':>9} {'med mfe_r':>9} "
           f"{'mfe%(abs)':>10} {'day-HL%':>8}")
    for wb in ["SMALL OR", "MID OR", "WIDE OR"]:
        g = sw[sw["wbin"] == wb]
        b = g[g["broke"]].copy()
        b["mfe_pct"] = b["mfe_r"] * b["or_range_pct"]
        _print(f"    {wb:>9} {g['or_range_pct'].median():>6.2f}% | {100*g['broke'].mean():>6.0f}% "
               f"{100*b['true_run'].mean():>8.0f}% {b['mfe_r'].median():>9.2f} "
               f"{b['mfe_pct'].median():>9.2f}% {b['day_hl_pct'].median():>7.2f}%")
    rr = sw["or_range_pct"].corr(sw["day_hl_pct"], method="spearman")
    _print(f"    ρ(OR_range%, day-HL%) = {rr:+.3f}  → POSITIVE = small OR → SMALL day (persistence, "
           "NOT coiled); NEGATIVE = small OR → big day (coiled-spring, supports the hypothesis)")

    # ---- Controls -----------------------------------------------------------------------------
    _print("\n(Controls, 30-min non-expiry breakout days):")
    p_iv = _partial(bo["conc"], bo["mfe_r"], bo["atm_iv"])
    ic = bo["conc"].corr(bo["mfe_r"], method="spearman")
    _print(f"  • VOL-partial: ρ(pin-conc, mfe_r) = {ic:+.3f}, partial | atm_iv = {p_iv:+.3f}  "
           f"(does pin add beyond vol? ~0 ⇒ it's just the vol regime)")
    _print(f"  • Unconditional TRUE-run rate (30-min break) = {100*bo['true_run'].mean():.0f}%  "
           f"(base rate the pin buckets are measured against)")
    if plc is not None and not plc.empty:
        pb = plc[plc["broke"]]
        _print(f"  • PLACEBO mid-day 11:00 range (same 30-min width): TRUE-run "
               f"{100*pb['true_run'].mean():.0f}%  vs OPENING {100*bo['true_run'].mean():.0f}% "
               f"— OR is special only if clearly higher")
    # OOS walk-forward on the primary
    days = sorted(s30["day"].unique())
    cut = days[int(len(days) * 0.70)]
    for lbl, part in [("train <70%", s30[s30["day"] < cut]), ("test ≥70%", s30[s30["day"] >= cut])]:
        part = part.copy(); part["pin"] = _pin_bucket(part)
        hi = part[(part["pin"] == "HIGH (SIDEWAYS)") & part["broke"]]["true_run"]
        lo = part[(part["pin"] == "LOW (TRENDY)") & part["broke"]]["true_run"]
        _print(f"  • OOS {lbl:>10}: TRUE-run HIGH-pin {100*hi.mean():.0f}% vs LOW-pin "
               f"{100*lo.mean():.0f}%  (spread {100*(lo.mean()-hi.mean()):+.0f}pp)")

    # ---- Expiry (charm pin), 30-min brief -----------------------------------------------------
    ex = df[(df["expiry"]) & (df["W"] == 30)].copy()
    if len(ex) > 60:
        ex["pin"] = _pin_bucket(ex)
        _print("\n(Expiry ≤1.5 DTE, 30-min, |charm| pin) TRUE-run% of breakout days:")
        for pin in ["HIGH (SIDEWAYS)", "MID", "LOW (TRENDY)"]:
            b = ex[(ex["pin"] == pin) & ex["broke"]]
            _print(f"    {pin:>16}: {100*b['true_run'].mean():>4.0f}%  (n_break={len(b)})")

    _print("\n  Read: LOW-pin (TRENDY) should break more & run truer; HIGH-pin (SIDEWAYS) holds the "
           "range / fakes. It's a REAL edge only if it survives the vol-partial (>0), beats the "
           "placebo band, and holds OOS — else ORB just re-expresses the vol regime. Sign-free.")


if __name__ == "__main__":
    main()
