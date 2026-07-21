"""Study #29 — the VALUE PROFILE at the *option-expiry-weekly* frequency, vs Fast-TRUE gamma blasts.

Every prior TPO study (#25, #27, #28) built the profile from a SINGLE session (the expiry-day
morning ≤ the lock). Here the frequency is the **expiry week**: for a NIFTY Tuesday expiry the week
runs Wednesday → Tuesday (the day after the previous expiry, through this expiry). We aggregate the
1-min spot across that whole week and compute ONE POC / value area for it — a full week of price
acceptance, a more stable value read than a half-day developing profile.

Point-in-time: the weekly profile is frozen at the expiry-day 13:45 lock — every prior week-day's
FULL session plus the expiry morning ≤ 13:45. The Fast-TRUE outcome is a 0-DTE crossing that fires
strictly AFTER (≥13:45), so features never see it.

"Fast-TRUE" (from analyze_blast_speed.py): a 0-DTE afternoon (≥13:45) strike-crossing whose
expanding-window grade reached a sustaining score within ≤10 min of the cross (k_true ∈ {1,2}).
An expiry day is FAST-TRUE if at least one afternoon crossing ignited that fast; base rate ≈ 48%
(much more selective / balanced than plain-TRUE's 77%, so lifts are readable).

Question: does WHERE spot / the expiry pin sits relative to the WEEKLY value area — and the weekly
day-type (double-distribution, VA width) — separate the Fast-TRUE expiries from the rest?

  python scripts/analyze_weekly_profile_blast.py            # NIFTY

Data: Dhan 1-min store (spot + per-strike OI); pin = max-pain @13:45; weekly profile = tpo_histogram
over the expiry-week spot ≤13:45; FAST-TRUE from data/journal/blast_ml/<store>.parquet. Sign-free;
the afternoon move is a MID/underlying magnitude (real fills worse). Decision-support, not a rule."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCK_T = "13:45"
WEEK_MAX_GAP = 9          # a week-day must be within 9 calendar days of the expiry (drop cache gaps)
FAST_KTRUE = {1, 2}       # k_true ∈ {1,2} = ignited within ≤10 min of the cross


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _mins(t: str) -> int:
    h, m = str(t).split(":")
    return int(h) * 60 + int(m)


def _snap_at(df, hhmm):
    ts = sorted((t for t in df["time_ist"].unique() if _mins(t) <= _mins(hhmm)), key=_mins)
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


def _binom_p(k, n, p0=0.5):
    """Two-sided exact binomial tail vs p0."""
    from math import comb
    if n == 0:
        return float("nan")
    pmf = [comb(n, i) * p0 ** i * (1 - p0) ** (n - i) for i in range(n + 1)]
    return sum(pv for i, pv in enumerate(pmf) if pv <= pmf[k] + 1e-12)


def _rate(sub):
    n = len(sub)
    if not n:
        return "  (n=0)"
    ft = (sub["fast_true"]).mean()
    tr = (sub["true"]).mean()
    return f"n={n:>3}   FAST-TRUE {100*ft:>3.0f}%   (any-TRUE {100*tr:>3.0f}%)"


def _fast_true_labels(store):
    """{day: (fast_true, true)} for expiry days with 0-DTE afternoon (≥13:45) crossings."""
    import pandas as pd
    p = ROOT / "data" / "journal" / "blast_ml" / f"{store}.parquet"
    if not p.exists():
        return {}
    df = pd.read_parquet(p, columns=["day", "dte", "tod", "k_true"])
    aft = df[(df["dte"] < 0.6) & (df["tod"] >= _mins(LOCK_T))]
    out = {}
    for d, s in aft.groupby(aft["day"].astype(str))["k_true"]:
        out[d] = (bool(s.isin(FAST_KTRUE).any()), bool(s.notna().any()))
    return out


def _week_days(exp, prev, all_days):
    """Intraday trading days in the expiry WEEK: after the prior expiry, up to & incl. ``exp``,
    within WEEK_MAX_GAP calendar days of it (so a feature-cache gap can't glue two weeks)."""
    e = date.fromisoformat(exp)
    p = date.fromisoformat(prev) if prev else None
    out = []
    for d in all_days:
        if d > exp:
            break
        dd = date.fromisoformat(d)
        if (p is None or dd > p) and 0 <= (e - dd).days <= WEEK_MAX_GAP:
            out.append(d)
    return out


def _weekly_spot(store, week_days, exp):
    """Point-in-time expiry-week 1-min spot ≤ the lock: each prior week-day's FULL clean session
    plus the expiry-day morning ≤ LOCK_T. Returns a concatenated np.array (or None)."""
    import numpy as np
    from app.market_profile import spot_path
    from data.dhan import load_intraday_day

    arrs = []
    for d in week_days:
        if d == exp:
            df = load_intraday_day(store, d)
            if df is None or df.empty:
                continue
            sp = df.groupby("time_ist")["spot"].first()
            sp = sp[[t for t in sp.index if _mins(t) <= _mins(LOCK_T)]]
            import pandas as pd
            sp = pd.to_numeric(sp, errors="coerce").dropna()
            if len(sp):
                arrs.append(sp.to_numpy())
        else:
            s = spot_path(store, d)          # clean-tick full session (drops bad-tick days)
            if s is not None:
                arrs.append(s.to_numpy())
    return np.concatenate(arrs) if arrs else None


def _weekly_bin(arr, base_bp, n_target=45):
    """A weekly-scale bin width. The default 5-pt session bin over a whole week's ~500-pt range
    gives ~100+ fine bins, so ``_count_modes`` (tuned for a ~40-bin session) reads every jitter as
    a mode → 'double-distribution' saturates at 100%. Snap the bin to a multiple of the base step
    so the weekly histogram has ~``n_target`` bins, matching a session's resolution — then POC/VA
    and the mode count are read at the same grain the shape stats were designed for."""
    import numpy as np
    rng = float(np.nanmax(arr) - np.nanmin(arr))
    if rng <= 0:
        return base_bp
    raw = rng / n_target
    return max(base_bp, round(raw / base_bp) * base_bp)


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.market_profile import BIN_PTS, tpo_histogram
    from data.dhan import available_intraday_days, load_intraday_day
    from data.replay import _expiry_cache

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    bp = BIN_PTS.get(store, 5.0)

    labels = _fast_true_labels(store)
    if not labels:
        _print(f"No blast_ml labels for {store} — build it first (build_blast_dataset.py {store})."); return
    _, expd = _expiry_cache(store)
    exps = sorted(expd)
    all_days = available_intraday_days(store)
    prev_of = {exps[i]: (exps[i - 1] if i else None) for i in range(len(exps))}

    rows = []
    for exp in exps:
        if exp not in labels:
            continue
        wd = _week_days(exp, prev_of.get(exp), all_days)
        if len(wd) < 2:                       # need a real week, not just the expiry morning
            continue
        arr = _weekly_spot(store, wd, exp)
        if arr is None or len(arr) < 60:
            continue
        wbp = _weekly_bin(arr, bp)           # coarser, weekly-scale bin (session-comparable grain)
        prof = tpo_histogram(pd.Series(arr), bin_pts=wbp)
        if prof is None:
            continue
        df = load_intraday_day(store, exp)
        if df is None or df.empty:
            continue
        snap = _snap_at(df, LOCK_T)
        if snap is None or snap.empty:
            continue
        pin = _max_pain(snap)
        if pin is None:
            continue
        morn = df.groupby("time_ist")["spot"].first()
        morn = pd.to_numeric(morn[[t for t in morn.index if _mins(t) <= _mins(LOCK_T)]],
                             errors="coerce").dropna()
        if morn.empty:
            continue
        spot_l, pin = float(morn.iloc[-1]), float(pin)
        poc, vah, val, vaw = prof["poc"], prof["vah"], prof["val"], prof["va_width"]
        fast_true, true = labels[exp]
        rows.append({
            "exp": exp, "n_days": len(wd), "w_bp": wbp, "fast_true": fast_true, "true": true,
            "spot_l": spot_l, "pin": pin, "poc": poc, "vah": vah, "val": val,
            "w_vaw": vaw, "w_vaw_pct": prof["va_width_pct"], "w_modes": prof["n_modes"],
            "w_double": prof["n_modes"] >= 2,
            "spot_out_va": bool(spot_l < val or spot_l > vah),
            "pin_out_va": bool(pin < val or pin > vah),
            "dist_pp": abs(pin - poc),
            "dist_pp_vaw": (abs(pin - poc) / vaw) if vaw else np.nan,
        })

    R = pd.DataFrame(rows)
    if R.empty:
        _print("No usable weekly-profiled expiries."); return
    base = R["fast_true"].mean()
    baseT = R["true"].mean()
    _print(f"Study #29 · WEEKLY (expiry-week) value profile vs Fast-TRUE blast · {store} · "
           f"{len(R)} expiries ({R.exp.iloc[0]}→{R.exp.iloc[-1]}) · lock {LOCK_T}")
    _print(f"expiry-week = day-after-prior-expiry → expiry (Wed→Tue), median {R.n_days.median():.0f} "
           f"trading days; profile frozen at {LOCK_T} (point-in-time)")
    _print(f"base rate: FAST-TRUE {100*base:.0f}%   ·   any-TRUE {100*baseT:.0f}%\n")

    _print("Weekly profile shape (frozen at lock):")
    _print(f"    median VA width {R.w_vaw.median():.0f} pt  ·  VA/week-range "
           f"{100*R.w_vaw_pct.median():.0f}%  ·  double-distribution weeks "
           f"{100*R.w_double.mean():.0f}%  ·  |pin−wPOC| median {R.dist_pp.median():.0f} pt  ·  "
           f"median weekly bin {R.w_bp.median():.0f} pt\n")

    # (A) spot vs weekly VA
    _print("(A) At the lock — is SPOT inside or outside the WEEKLY value area?")
    _print(f"    spot OUTSIDE weekly VA : {_rate(R[R.spot_out_va])}")
    _print(f"    spot INSIDE  weekly VA : {_rate(R[~R.spot_out_va])}")
    a, b = R[R.spot_out_va], R[~R.spot_out_va]
    _print(f"    → FAST-TRUE lift outside vs inside: "
           f"{100*(a.fast_true.mean()-b.fast_true.mean()):+.0f} pp\n")

    # (B) pin vs weekly VA
    _print("(B) Is the expiry PIN inside or outside the WEEKLY value area?")
    _print(f"    pin OUTSIDE weekly VA : {_rate(R[R.pin_out_va])}")
    _print(f"    pin INSIDE  weekly VA : {_rate(R[~R.pin_out_va])}")
    a, b = R[R.pin_out_va], R[~R.pin_out_va]
    _print(f"    → FAST-TRUE lift outside vs inside: "
           f"{100*(a.fast_true.mean()-b.fast_true.mean()):+.0f} pp\n")

    # (C) |pin - weekly POC| terciles
    _print("(C) Larger |pin − weekly POC| → faster ignition? (terciles)")
    R["pp_t"] = pd.qcut(R["dist_pp"].rank(method="first"), 3, labels=["low", "mid", "high"])
    for t in ["low", "mid", "high"]:
        sub = R[R.pp_t == t]
        rng = f"[{sub.dist_pp.min():.0f}-{sub.dist_pp.max():.0f}pt]"
        _print(f"    {t:<4} |pin−wPOC| {rng:<14} {_rate(sub)}")
    ic = R[["dist_pp"]].assign(t=R.fast_true.astype(int)).corr(method="spearman").iloc[0, 1]
    _print(f"    Spearman IC(|pin−wPOC|, FAST-TRUE) = {ic:+.2f}\n")

    # (D) weekly day-type shape. The #28 binary double-distribution SATURATES at weekly frequency
    # (a week almost always has ≥2 balance areas), so it can't discriminate — the meaningful weekly
    # shape axis is VA width / week range: a NARROW value area (low %) = the week trended/relocated
    # (directional), a WIDE one = rotational/balanced.
    _print(f"(D) WEEKLY shape. (double-distribution binary saturates: "
           f"{100*R.w_double.mean():.0f}% of weeks ≥2 modes — not discriminating.)")
    _print("    Meaningful axis = VA width / week range (narrow = directional week):")
    R["vw_t"] = pd.qcut(R["w_vaw_pct"].rank(method="first"), 3, labels=["narrow", "mid", "wide"])
    for t in ["narrow", "mid", "wide"]:
        sub = R[R.vw_t == t]
        rng = f"[{100*sub.w_vaw_pct.min():.0f}-{100*sub.w_vaw_pct.max():.0f}%]"
        _print(f"    {t:<7} VA/range {rng:<12} {_rate(sub)}")
    ic = R[["w_vaw_pct"]].assign(t=R.fast_true.astype(int)).corr(method="spearman").iloc[0, 1]
    _print(f"    Spearman IC(VA/range, FAST-TRUE) = {ic:+.2f}\n")

    # (E) combined setup — built from the signals that actually LIFTED here (spot out of weekly value
    # + a wide pin↔wPOC gap), NOT the #28 morning recipe (pin-out-VA / double-dist both died weekly).
    setup = R[R.spot_out_va & (R.dist_pp >= R.dist_pp.median())]
    rest = R.drop(setup.index)
    _print("(E) COMBINED setup — SPOT outside weekly VA · |pin−wPOC| ≥ median:")
    _print(f"    setup          : {_rate(setup)}")
    _print(f"    everything else: {_rate(rest)}")
    if len(setup):
        k = int(setup.fast_true.sum())
        _print(f"    → setup FAST-TRUE {100*setup.fast_true.mean():.0f}% vs base {100*base:.0f}%  "
               f"(n={len(setup)}, p vs base={_binom_p(k, len(setup), base):.2f})\n")

    # per-expiry table (most recent 16) — the actual weekly POC/VA the user asked to compute
    _print("Recent expiries — computed WEEKLY POC/VA (@lock) + label:")
    _print(f"    {'expiry':<11}{'d':>2} {'wVAL':>7}{'wPOC':>7}{'wVAH':>7} {'spot':>7}{'pin':>7}  "
           f"{'spot?':<7}{'pin?':<7}{'modes':>5}  label")
    for r in R.tail(16).itertuples():
        lab = "FAST-TRUE" if r.fast_true else ("TRUE(slow)" if r.true else "FAKE")
        _print(f"    {r.exp:<11}{r.n_days:>2} {r.val:>7.0f}{r.poc:>7.0f}{r.vah:>7.0f} "
               f"{r.spot_l:>7.0f}{r.pin:>7.0f}  {'OUT' if r.spot_out_va else 'in':<7}"
               f"{'OUT' if r.pin_out_va else 'in':<7}{r.w_modes:>5}  {lab}")

    _print("\n  Read: a claim SURVIVES if its 'effective' cell has a clearly higher FAST-TRUE rate "
           "beyond the ~48% base with enough n. The weekly profile is a slower, more stable value "
           "read than the #28 morning profile — if the same pin-out-of-value / double-distribution "
           "geometry shows up HERE too, it's the same mild trend-day tendency at a steadier frequency, "
           "not a new edge. Sign-free; decision-support, not a trade rule.")


if __name__ == "__main__":
    main()
