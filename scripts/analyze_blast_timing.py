"""1-MINUTE ignition timing: how fast does a crossing reach TRUE, and is there an early-score
cutoff (esp. within WEAK) that foretells it? Direction is not the question — the premium jump IS
the direction — so this is purely about TIMING the winning-leg premium expansion, at 1-min.

The 5-min dataset (build_blast_dataset) downsamples the genuinely 1-min Dhan store (premium AND OI
both update every minute). This reads the raw store at 1-min, detects each first near-money crossing
(parity CE≈PE), and builds the blast-score TRAJECTORY minute-by-minute from the cross:

  k_true_min = first MINUTE the expanding-window grade reaches TRUE (score≥60), ≤SEARCH min else never
  score@1/3/5 = the early read at 1/3/5 min (for the WEAK cutoff)
  outcome     = winning (ITM-going) leg premium multiple over a FIXED window AFTER the trigger

Point-in-time (k_true is elapsed at the trigger; outcome measured after). 0-DTE only (blast lives
there). Sign-free. ⚠️ MID-peak multiple — real expiry-afternoon fills are worse.

  python scripts/analyze_blast_timing.py            # NIFTY  [good_mult=1.5]"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEARCH = 15          # search first-TRUE within 15 min of the cross (blasts are fast)
FWD = 15             # winning-leg outcome window (min) AFTER the trigger
NEAR = 3             # crossings within ±NEAR strikes of spot
PREM_FLOOR = 2.0     # entry (trigger) winning-leg premium floor, pts (screen penny inflation)
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


def _day_rows(store, day, dte):
    import numpy as np
    from data.dhan import load_intraday_day
    from app.gammablast import grade_blast

    df = load_intraday_day(store, day)
    if df is None or df.empty:
        return []
    stamps = sorted(df["time_ist"].unique(), key=_mins)
    idx = {t: i for i, t in enumerate(stamps)}
    n = len(stamps)
    if n < SEARCH + FWD + 5:
        return []
    spot = np.full(n, np.nan)
    for t, v in df.groupby("time_ist")["spot"].first().items():
        spot[idx[t]] = v
    CL, PL, CO, PO = {}, {}, {}, {}

    def _ser(g, col):
        a = np.full(n, np.nan)
        for t, v in g.set_index("time_ist")[col].items():
            if t in idx:
                a[idx[t]] = v
        return a
    for (K, sd), g in df.groupby(["strike", "side"]):
        g = g.drop_duplicates("time_ist")
        if sd == "CE":
            CL[K], CO[K] = _ser(g, "close"), _ser(g, "oi")
        else:
            PL[K], PO[K] = _ser(g, "close"), _ser(g, "oi")
    strikes = sorted(K for K in CL if K in PL)
    if len(strikes) < 5:
        return []
    step = min((b - a) for a, b in zip(strikes, strikes[1:]))

    def _frac(a, b):
        return (b - a) / a if (a and a == a and b == b) else 0.0

    rows = []
    for K in strikes:
        cl, pl = CL[K], PL[K]
        diff = np.where((cl == cl) & (pl == pl), cl - pl, np.nan)
        seen = set()
        for i in range(1, n):
            a, b = diff[i - 1], diff[i]
            if not (a == a and b == b):
                continue
            up = a < 0 <= b
            dn = a > 0 >= b
            if not (up or dn) or (K, up) in seen:
                continue
            if abs(K - spot[i]) > NEAR * step or i + SEARCH + FWD >= n:
                continue
            seen.add((K, up))
            dirs = 1.0 if up else -1.0
            itm_l = cl if up else pl
            itm_o = CO[K] if up else PO[K]
            Kah = K + step if up else K - step
            aoi = (CO.get(Kah) if up else PO.get(Kah))
            scores = {}
            for m in range(1, SEARCH + 1):
                j = i + m
                if itm_o[j] != itm_o[j] or itm_l[j] != itm_l[j]:
                    continue
                wr = (aoi[j] / itm_o[j]) if (aoi is not None and itm_o[j] and aoi[j] == aoi[j]) else 0.0
                we = _frac(aoi[i], aoi[j]) if aoi is not None else 0.0
                scores[m] = grade_blast(_frac(itm_o[i], itm_o[j]), _frac(itm_l[i], itm_l[j]), wr, we)["score"]
            k_true = next((m for m in range(1, SEARCH + 1) if scores.get(m, -1) >= 60), np.nan)
            cont_after = mult_after = np.nan
            if k_true == k_true:
                trig = i + int(k_true)
                end = trig + FWD
                tp = itm_l[trig]
                if end < n and spot[trig] == spot[trig] and spot[end] == spot[end] and tp and tp == tp and tp >= PREM_FLOOR:
                    cont_after = dirs * (spot[end] - spot[trig]) / spot[trig] * 100
                    seg = itm_l[trig:end + 1]
                    mult_after = float(np.nanmax(seg)) / tp
            rows.append({"day": day, "dte": dte, "tod": _mins(stamps[i]),
                         "k_true": k_true, "score1": scores.get(1, np.nan),
                         "score3": scores.get(3, np.nan), "score5": scores.get(5, np.nan),
                         "cont_after": cont_after, "mult_after": mult_after})
    return rows


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.expiry_regime import ERA_LABELS, era_order, expiry_era
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    good = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
    feat = load_features(store)
    if feat is None:
        _print("No feature cache."); return
    dte_by_day = feat.groupby("day")["dte"].first().to_dict()
    days = sorted(d for d, v in dte_by_day.items() if v <= 1.5)      # 0-DTE only
    _print(f"1-min ignition timing · {store} · {len(days)} 0-DTE days · search≤{SEARCH}m, "
           f"outcome {FWD}m post-trigger · good=≥{good:g}×\n")
    rows = []
    for di, d in enumerate(days, 1):
        rows.extend(_day_rows(store, d, dte_by_day[d]))
        if di % 60 == 0:
            _print(f"  …{di}/{len(days)} days, {len(rows)} crossings")
    df = pd.DataFrame(rows)
    if df.empty:
        _print("No crossings."); return
    df["era"] = df["day"].map(expiry_era)
    ever = df["k_true"].notna()
    aft = df[df["tod"] >= 13 * 60 + 45]

    _print(f"\ncrossings: {len(df):,} ({df['day'].nunique()} days) · reached TRUE ≤{SEARCH}m: "
           f"{100*ever.mean():.0f}% · median time-to-TRUE: {df.loc[ever,'k_true'].median():.0f} min")
    _print("\n(1) Time-to-TRUE distribution at 1-MIN (share of all crossings):")
    cells = [f"{m}m {100*(df['k_true']==m).mean():.0f}%" for m in (1, 2, 3, 4, 5)]
    cells += [f"6-10m {100*df['k_true'].between(6,10).mean():.0f}%",
              f"11-15m {100*df['k_true'].between(11,15).mean():.0f}%",
              f"never {100*(~ever).mean():.0f}%"]
    _print("  " + "  ".join(cells))
    _print(f"  ≤5 min of all crossings: {100*df['k_true'].between(1,5).mean():.0f}%  ·  "
           f"of those that EVER ignite: {100*df.loc[ever,'k_true'].between(1,5).mean():.0f}%")

    def _bucket(sub, title):
        _print("\n" + title)
        _print(f"  {'time-to-TRUE':<16} {'n':>5} | {'medMult':>8} {'≥'+format(good,'g')+'×':>6} "
               f"{'≥2×':>5} {'medCont':>9}")
        for lbl, lo, hi in [("≤5 min", 1, 5), ("6-10 min", 6, 10), ("11-15 min", 11, 15)]:
            g = sub[sub["k_true"].between(lo, hi)]
            mp = g["mult_after"].dropna(); ca = g["cont_after"].dropna()
            if len(mp) == 0:
                _print(f"  {lbl:<16} {len(g):>5} | (no outcome)"); continue
            _print(f"  {lbl:<16} {len(g):>5} | {mp.median():>7.2f}× {100*(mp>=good).mean():>5.0f}% "
                   f"{100*(mp>=2).mean():>4.0f}% {ca.median():>+8.3f}%")
    _bucket(df, "(2) Post-trigger outcome by time-to-TRUE — ALL 0-DTE crossings:")
    _bucket(aft, "(2b) same — 0-DTE AFTERNOON (≥13:45):")

    # (3) EARLY-SCORE cutoff: does the score at 3 min foretell reaching TRUE / a good blast?
    _print("\n(3) Early read — score at 3 MIN → P(reach TRUE ≤15m) and good-blast rate (0-DTE afternoon):")
    _print(f"  {'score@3min band':<18} {'n':>5} {'reachTRUE':>10} {'≤5m':>6} | "
           f"{'good≥'+format(good,'g')+'×':>8}  (of reached)")
    bands = [("<25 (dead)", -1, 25), ("25-34", 25, 35), ("35-44 WEAK-lo", 35, 45),
             ("45-54 WEAK-hi", 45, 55), ("55-59 near-TRUE", 55, 60), ("≥60 TRUE@3m", 60, 999)]
    for lbl, lo, hi in bands:
        g = aft[(aft["score3"] >= lo) & (aft["score3"] < hi)]
        if len(g) == 0:
            _print(f"  {lbl:<18} {0:>5}"); continue
        rc = g[g["k_true"].notna()]
        p_reach = 100.0 * len(rc) / len(g)
        p_fast = 100.0 * rc["k_true"].between(1, 5).mean() if len(rc) else float("nan")
        mp = rc["mult_after"].dropna()
        pg = f"{100*(mp>=good).mean():.0f}%" if len(mp) else "  —"
        _print(f"  {lbl:<18} {len(g):>5} {p_reach:>9.0f}% {p_fast:>5.0f}% | {pg:>8}")

    _print("\n(4) 0-DTE afternoon by expiry-structure era — ≤5-min-TRUE rate & its good-blast:")
    _print(f"    {'era':<42} {'n':>5} {'≤5m-TRUE':>9} {'good≥'+format(good,'g')+'×':>9}")
    for e in era_order(aft["era"].unique()):
        sub = aft[aft["era"] == e]
        fast = sub[sub["k_true"].between(1, 5)]
        mp = fast["mult_after"].dropna()
        pg = f"{100*(mp>=good).mean():.0f}%" if len(mp) else "—"
        _print(f"    {ERA_LABELS[e]:<42} {len(sub):>5} {100*sub['k_true'].between(1,5).mean():>8.0f}% {pg:>9}")

    _print("\n  Read: with 1-min resolution we can see sub-5-min ignition. A cutoff edge ⇒ a higher "
           "score@3min sharply raises P(reach TRUE) AND the good-blast rate, and ≤5-min-TRUE beats "
           "slower — holding in Sep25+. ⚠️ 1-min OI is genuine but noisier over 1-2 min; MID-peak "
           "multiple (real fills worse); first-TRUE trigger; sign-free.")


if __name__ == "__main__":
    main()
