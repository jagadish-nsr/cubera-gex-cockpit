"""Study #27 — Market Profile × expiry pin: the mid-day "run to the pin" naked-option trade,
and whether POC↔pin distance / morning TPO shape inform the TRUE-blast cases.

Two questions the user posed, both on expiry (0-DTE) days, both sign-free:

Q1  THE TRADE. At mid-day, WHEN spot is sitting on the developing POC (the TPO point-of-control
    from the morning session), buy a NAKED option struck at the expiry pin (max-pain), in the pin's
    direction:  pin ABOVE spot → CALL,  pin BELOW spot → PUT.  Hold to the close. Is it profitable?
    P&L is the REAL 1-min traded premium of that exact pin strike (entry LTP → close LTP), so theta
    and the move are both in it. Controls: (a) the SAME trade without the "spot on POC" filter, and
    (b) a MIRROR placebo — same strike distance on the WRONG side — so we can tell a genuine pin pull
    from just "buy any cheap 0-DTE option and hope."
    ⚠ Subtlety kept honest: an option struck AT the pin pays only if price OVERSHOOTS the pin; if the
    pin is a true magnet and price stops on it, the pin-strike option decays to ~0. We report both the
    premium P&L and the sign-free "did spot move toward the pin" hit-rate so the two don't get conflated.

Q2  DOES STRUCTURE INFORM THE TRUE BLASTS? Among crossings the blast grade calls TRUE (from the
    blast_ml dataset), does (i) |POC − pin| at mid-day or (ii) the MORNING TPO distribution shape
    (VA-width%, mode count, POC concentration) carry information about the post-trigger continuation
    (`cont_after`) or the winning-leg premium multiple (`mult_after_peak`)? Point-in-time: morning
    profile + pin are frozen at ENTRY_T (noon-ish); only afternoon TRUE crossings (after ENTRY_T) are
    scored, so the feature never sees its own outcome. Reported as Spearman IC with a coin-flip read.

  python scripts/analyze_pin_poc_trade.py                 # NIFTY, entry 12:15
  python scripts/analyze_pin_poc_trade.py NIFTY 12:15 30  # store, entry time, last-N expiries (0=all)

Data: Dhan 1-min per-strike store (spot + CE/PE LTP + OI). Pin = max-pain over the frozen ENTRY_T
snapshot. POC/VA = app.market_profile.tpo_histogram on the morning spot. Expiry days = open dte<0.6.
Sign-free, decision-support ONLY — premiums are MID/LTP (real expiry-afternoon fills are worse). Not a
trade rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ENTRY_T = "12:15"        # mid-day entry (session midpoint ≈ 12:22)
POC_TOL = 25.0           # "spot on POC" = within half a strike
PIN_MIN = 25.0           # need at least half a strike of pin distance to have a directional bet
PREM_FLOOR = 2.0         # skip penny entry premiums (divide-by-noise)
STRIKE = 50.0


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _snap_at(df, hhmm):
    ts = sorted(t for t in df["time_ist"].unique() if t <= hhmm)
    return df[df["time_ist"] == ts[-1]] if ts else None


def _max_pain(snap):
    coi = snap[snap["side"] == "CE"].groupby("strike")["oi"].sum()
    poi = snap[snap["side"] == "PE"].groupby("strike")["oi"].sum()
    strikes = sorted(set(coi.index) | set(poi.index))
    best_k = best_pain = None
    for kexp in strikes:
        pain = 0.0
        for k in strikes:
            if k < kexp:
                pain += float(coi.get(k, 0.0)) * (kexp - k)
            elif k > kexp:
                pain += float(poi.get(k, 0.0)) * (k - kexp)
        if best_pain is None or pain < best_pain:
            best_pain, best_k = pain, kexp
    return best_k


def _ltp(snap, strike, side):
    row = snap[(snap["strike"] == strike) & (snap["side"] == side)]
    if row.empty:
        return None
    v = float(row["close"].iloc[0])
    return v if v == v and v > 0 else None


def _leg_return(df, strike, side, entry_snap, last_t):
    """Real premium P&L for buying (strike, side) at ENTRY_T and selling at the close."""
    ep = _ltp(entry_snap, strike, side)
    xp = _ltp(_snap_at(df, last_t), strike, side)
    if ep is None or ep < PREM_FLOOR or xp is None:
        return None
    return xp / ep - 1.0


def _pnl_stats(rets):
    import numpy as np
    r = np.asarray([x for x in rets if x is not None], dtype=float)
    r = r[np.isfinite(r)]                       # drop missing legs (None → NaN via pandas)
    if len(r) == 0:
        return None
    return {"n": len(r), "win": 100 * (r > 0).mean(), "mean": 100 * r.mean(),
            "med": 100 * np.median(r), "p90": 100 * np.quantile(r, 0.9),
            "best": 100 * r.max(), "worst": 100 * r.min()}


def _line(tag, s):
    if s is None:
        _print(f"  {tag:<34} (no trades)"); return
    _print(f"  {tag:<34} n={s['n']:>3}  win {s['win']:>3.0f}%  mean {s['mean']:>+6.1f}%  "
           f"med {s['med']:>+6.1f}%  p90 {s['p90']:>+6.0f}%  best {s['best']:>+5.0f}%  worst {s['worst']:>+4.0f}%")


def _spearman(x, y):
    import numpy as np
    from scipy.stats import spearmanr
    x, y = np.asarray(x, float), np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 12:
        return float("nan"), int(m.sum())
    return float(spearmanr(x[m], y[m]).correlation), int(m.sum())


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.market_profile import BIN_PTS, tpo_histogram
    from data.dhan import load_intraday_day
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    entry_t = sys.argv[2] if len(sys.argv) > 2 else ENTRY_T
    n_exp = int(sys.argv[3]) if len(sys.argv) > 3 else 0            # 0 = all 0-DTE days
    bp = BIN_PTS.get(store, 5.0)

    feat = load_features(store)
    if feat is None:
        _print("No feature cache."); return
    dte0 = feat.groupby("day")["dte"].first()
    exp_days = sorted(d for d, v in dte0.items() if float(v) < 0.6)
    if n_exp:
        exp_days = exp_days[-n_exp:]

    day_rows = []                       # per-day record (trade + structure features)
    for d in exp_days:
        df = load_intraday_day(store, d)
        if df is None or df.empty:
            continue
        sp = df.groupby("time_ist")["spot"].first().sort_index()
        morn = sp[sp.index <= entry_t]
        if len(morn) < 30:
            continue
        prof = tpo_histogram(morn, bin_pts=bp)
        entry_snap = _snap_at(df, entry_t)
        if prof is None or entry_snap is None or entry_snap.empty:
            continue
        spot_e = float(morn.iloc[-1])
        pin = _max_pain(entry_snap)
        if pin is None:
            continue
        last_t = max(df["time_ist"].unique())
        close_spot = float(sp.loc[last_t])
        poc = float(prof["poc"])
        gap = pin - spot_e                                      # signed pin distance
        if abs(gap) < PIN_MIN:
            continue
        call = gap > 0
        side = "CE" if call else "PE"
        side_mirror = "PE" if call else "CE"
        strike = float(pin)                                    # the pin strike (max-pain is a listed strike)
        strike_mirror = spot_e + (-abs(gap) if call else abs(gap))
        strike_mirror = round(strike_mirror / STRIKE) * STRIKE  # same distance, wrong side
        ret = _leg_return(df, strike, side, entry_snap, last_t)
        ret_mir = _leg_return(df, strike_mirror, side_mirror, entry_snap, last_t)
        toward = np.sign(close_spot - spot_e) == np.sign(gap)
        day_rows.append({
            "day": d, "spot_e": spot_e, "poc": poc, "pin": pin, "close": close_spot,
            "gap": gap, "on_poc": abs(spot_e - poc) <= POC_TOL,
            "ret": ret, "ret_mir": ret_mir, "toward": bool(toward),
            "dist_poc_pin": abs(poc - pin),
            "va_width": prof["va_width"], "day_range": prof["day_range"],
            "va_width_pct": prof["va_width_pct"], "n_modes": prof["n_modes"],
            "tpoc_ratio": prof["tpoc_ratio"],
        })

    R = pd.DataFrame(day_rows)
    if R.empty:
        _print("No qualifying expiry days."); return
    # --- normalised "wide-VA" scores (all from the MORNING profile, known point-in-time) ---
    R["vaw_over_poc"] = R["va_width"] / R["poc"] * 100.0        # VA width as % of price (the user's VA/POC)
    R["vaw_z"] = (R["vaw_over_poc"] - R["vaw_over_poc"].mean()) / R["vaw_over_poc"].std()  # wide vs typical
    R["pin_poc_over_vaw"] = R["dist_poc_pin"] / R["va_width"]   # pin↔POC gap measured in VA-widths
    _print(f"Study #27 · Market Profile × expiry pin · {store} · entry {entry_t} · "
           f"{len(R)} expiries ({R['day'].iloc[0]}→{R['day'].iloc[-1]})\n")

    # ---- Q1: the naked pin-direction trade ------------------------------------------------
    on = R[R["on_poc"]]
    _print("Q1 · NAKED option at the PIN strike, in the pin's direction, mid-day → close "
           f"(premium P&L, real LTP; |pin−spot|≥{PIN_MIN:.0f}, entry prem≥{PREM_FLOOR:.0f}):")
    _print(f"  'spot on POC' at {entry_t}: {len(on)}/{len(R)} days (within ±{POC_TOL:.0f} pt)\n")
    _line("PIN-dir @ POC touch (the trade)", _pnl_stats(on["ret"]))
    _line("  └ MIRROR placebo (wrong side)", _pnl_stats(on["ret_mir"]))
    _line("PIN-dir · ALL days (no POC filter)", _pnl_stats(R["ret"]))
    _line("  └ MIRROR placebo · ALL days", _pnl_stats(R["ret_mir"]))
    tw = on["toward"]
    _print(f"\n  sign-free check — did spot move TOWARD the pin (mid-day→close), 'on POC' days: "
           f"{int(tw.sum())}/{len(tw)} ({100*tw.mean():.0f}%)  ·  ALL days "
           f"{int(R['toward'].sum())}/{len(R)} ({100*R['toward'].mean():.0f}%)  [coin-flip=50%]")
    _print("  Read: profitable ⇒ PIN-dir mean% clearly >0 AND beats the MIRROR. If mean%≤0 or ≈mirror, "
           "the pin strike decays faster than price reaches it (magnet ⇒ pin-struck option → 0).\n")

    # ---- Q2: does POC↔pin distance / morning shape inform the TRUE blasts? -----------------
    path = ROOT / "data" / "journal" / "blast_ml" / f"{store}.parquet"
    if not path.exists():
        _print(f"Q2 skipped — no {path.name} (build_blast_dataset.py)."); return
    bl = pd.read_parquet(path)
    if "k_true" not in bl.columns:
        _print("Q2 skipped — dataset lacks trajectory fields."); return
    cut_m = 12 * 60 + 15 if entry_t == ENTRY_T else int(entry_t[:2]) * 60 + int(entry_t[3:])
    # point-in-time: only afternoon crossings AFTER the entry snapshot, graded TRUE
    tru = bl[(bl["dte"] <= 1.5) & (bl["tod"] > cut_m) & (bl["k_true"].notna())].copy()
    feats = R.set_index("day")[["dist_poc_pin", "va_width_pct", "n_modes", "tpoc_ratio",
                                "vaw_over_poc", "vaw_z", "pin_poc_over_vaw"]]
    tru = tru.join(feats, on="day")
    tru = tru[tru["dist_poc_pin"].notna()]
    # day-collapsed: one row per day (mean outcome) to kill the crossing-level autocorrelation —
    # a per-crossing IC counts a busy day many times over, so the honest test is at the day level.
    _feat_cols = ["dist_poc_pin", "va_width_pct", "n_modes", "tpoc_ratio",
                  "vaw_over_poc", "vaw_z", "pin_poc_over_vaw"]
    agg = {"cont_after": ("cont_after", "mean"), "mult_after_peak": ("mult_after_peak", "mean")}
    agg.update({c: (c, "first") for c in _feat_cols})
    dcol = tru.groupby("day").agg(**agg).reset_index()
    _print(f"Q2 · within TRUE-graded 0-DTE afternoon crossings (>{entry_t}) — Spearman IC of a "
           f"structure feature vs the outcome:")
    _print(f"    per-crossing n = {len(tru)}  ·  day-collapsed n = {len(dcol)} distinct days\n")
    _print(f"  {'feature':<22} | {'per-crossing':>24} | {'DAY-collapsed (robust)':>24}")
    _print(f"  {'':<22} | {'cont':>11} {'mult':>12} | {'cont':>11} {'mult':>12}")
    for f, lbl in [("dist_poc_pin", "|POC − pin| (pts)"), ("va_width_pct", "VA-width / range"),
                   ("n_modes", "morning #modes"), ("tpoc_ratio", "morning POC concentr."),
                   ("vaw_over_poc", "VA-width / POC (%)"), ("vaw_z", "VA-width z-score"),
                   ("pin_poc_over_vaw", "|POC−pin| / VA-width")]:
        ic1, _ = _spearman(tru[f], tru["cont_after"])
        ic2, _ = _spearman(tru[f], tru["mult_after_peak"])
        d1, _ = _spearman(dcol[f], dcol["cont_after"])
        d2, _ = _spearman(dcol[f], dcol["mult_after_peak"])
        _print(f"  {lbl:<22} | {ic1:>+11.2f} {ic2:>+12.2f} | {d1:>+11.2f} {d2:>+12.2f}")
    _print("\n  Read: trust the DAY-collapsed column (per-crossing double-counts busy days). |IC|≳0.2 at "
           "~100 days would be a hint; near 0 = the POC↔pin gap / morning TPO shape add nothing to a TRUE "
           "blast's follow-through. A surviving morning-VA-width tilt would just be vol persistence "
           "(directional morning → directional afternoon), not a pin/POC edge. Sign-free; MID multiples "
           "(real fills worse). Decision-support, not a trade rule.")


if __name__ == "__main__":
    main()
