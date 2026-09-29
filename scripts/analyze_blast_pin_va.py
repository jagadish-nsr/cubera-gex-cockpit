"""Study #28 — the user's TRUE-blast setup: at the 13:45 lock, spot sits on the expiry pin, then
runs toward POC; the blast is EFFECTIVE when the pin is OUTSIDE the value area, |pin−POC| is large,
and the morning is a double-distribution. Validate each claim on the data.

This is the 13:45-lock, TRUE-vs-FAKE angle on Study #27's structure features (which measured
continuation WITHIN TRUE crossings). Here the unit is the EXPIRY DAY, split TRUE-GB vs FAKE-GB by the
blast_ml afternoon (≥13:45) 0-DTE crossings (TRUE = at least one reached a sustaining score), and the
features are frozen at 13:45 (point-in-time): the developing TPO profile (POC/VAH/VAL, mode count) and
the max-pain pin from that snapshot's OI.

Claims tested:
  (A) at 13:45 spot is CLOSE to the pin (vs its distance to POC), and by close it has moved TOWARD POC.
  (B) pin OUTSIDE the value area  → higher TRUE-GB rate + bigger afternoon move.
  (C) larger |pin − POC|          → higher TRUE-GB rate + bigger afternoon move (monotone by tercile).
  (D) double-distribution morning → higher TRUE-GB rate + bigger afternoon move.
  (E) the combined setup (pin outside VA · large |pin−POC| · double-dist) vs everything else.

  python scripts/analyze_blast_pin_va.py            # NIFTY

Data: Dhan 1-min store (spot + per-strike OI); pin = max-pain @13:45; profile = tpo_histogram on the
morning spot ≤13:45; TRUE/FAKE from data/journal/blast_ml/<store>.parquet. Sign-free; the afternoon
move is a MID/underlying magnitude (real fills worse). Decision-support, not a trade rule."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCK_T = "13:45"


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


def _rate(sub):
    n = len(sub)
    if not n:
        return "  (n=0)"
    tr = (sub["label"] == "TRUE").mean()
    mv = sub["aft_move"].median()
    tw = sub["toward_poc"].mean()
    return f"n={n:>3}  TRUE-GB {100*tr:>3.0f}%   aft-move med {mv:>4.2f}%   →POC {100*tw:>3.0f}%"


def _binom_p(k, n, p0=0.5):
    """Two-sided exact binomial tail vs p0."""
    from math import comb
    if n == 0:
        return float("nan")
    pmf = [comb(n, i) * p0 ** i * (1 - p0) ** (n - i) for i in range(n + 1)]
    return sum(pv for i, pv in enumerate(pmf) if pv <= pmf[k] + 1e-12)


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.market_profile import BIN_PTS, tpo_histogram
    from data.dhan import load_intraday_day
    from data.replay import _label_frame

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    bp = BIN_PTS.get(store, 5.0)

    lp = ROOT / "data" / "journal" / "blast_ml" / f"{store}.parquet"
    if not lp.exists():
        _print(f"No {lp.name} — build it first (build_blast_dataset.py {store})."); return
    labels = _label_frame(pd.read_parquet(lp, columns=["day", "dte", "tod", "k_true"]))
    if not labels:
        _print("No labelled expiry days."); return

    rows = []
    for d in sorted(labels):
        df = load_intraday_day(store, d)
        if df is None or df.empty:
            continue
        sp = df.groupby("time_ist")["spot"].first().sort_index()
        morn = sp[sp.index <= LOCK_T]
        snap = _snap_at(df, LOCK_T)
        if len(morn) < 30 or snap is None or snap.empty:
            continue
        prof = tpo_histogram(morn, bin_pts=bp)
        if prof is None:
            continue
        pin = _max_pain(snap)
        if pin is None:
            continue
        spot_l = float(morn.iloc[-1])
        close = float(sp.iloc[-1])
        poc, vah, val = prof["poc"], prof["vah"], prof["val"]
        rows.append({
            "day": d, "label": labels[d], "spot_l": spot_l, "close": close,
            "pin": float(pin), "poc": poc,
            "d_spot_pin": abs(spot_l - pin), "d_spot_poc": abs(spot_l - poc),
            "pin_out_va": bool(pin < val or pin > vah),
            "dist_pp": abs(float(pin) - poc),
            "double_dist": prof["n_modes"] >= 2, "n_modes": prof["n_modes"],
            "toward_poc": bool(np.sign(close - spot_l) == np.sign(poc - spot_l)),
            "aft_move": abs(close - spot_l) / spot_l * 100.0,
        })

    R = pd.DataFrame(rows)
    if R.empty:
        _print("No usable days."); return
    base = (R["label"] == "TRUE").mean()
    _print(f"Study #28 · TRUE-blast setup · {store} · {len(R)} labelled expiries "
           f"({R['day'].iloc[0]}→{R['day'].iloc[-1]}) · lock {LOCK_T}")
    _print(f"base rate: TRUE-GB {100*base:.0f}%  ·  FAKE-GB {100*(1-base):.0f}%\n")

    # (A) spot near the pin at lock, then toward POC?
    _print("(A) At 13:45 — is spot on the pin, and does it then move to POC?")
    _print(f"    median |spot−pin| = {R['d_spot_pin'].median():.0f} pt   vs   |spot−POC| = "
           f"{R['d_spot_poc'].median():.0f} pt   (spot sits nearer the {'PIN' if R['d_spot_pin'].median() < R['d_spot_poc'].median() else 'POC'})")
    tw, n = int(R['toward_poc'].sum()), len(R)
    _print(f"    moved TOWARD POC by close: {tw}/{n} ({100*tw/n:.0f}%)  [coin-flip 50%, p={_binom_p(tw,n):.2f}]")
    twT = R[R.label=="TRUE"]["toward_poc"]; twF = R[R.label=="FAKE"]["toward_poc"]
    _print(f"      TRUE-GB days {100*twT.mean():.0f}%  ·  FAKE-GB days {100*twF.mean():.0f}%\n")

    # (B) pin outside VA
    _print("(B) Pin OUTSIDE the value area → more effective blast?")
    _print(f"    pin OUTSIDE VA : {_rate(R[R.pin_out_va])}")
    _print(f"    pin INSIDE  VA : {_rate(R[~R.pin_out_va])}")
    a, b = R[R.pin_out_va], R[~R.pin_out_va]
    _print(f"    → TRUE-GB lift outside vs inside: {100*((a.label=='TRUE').mean()-(b.label=='TRUE').mean()):+.0f} pp\n")

    # (C) |pin - POC| terciles
    _print("(C) Larger |pin − POC| → more effective? (terciles)")
    R["pp_t"] = pd.qcut(R["dist_pp"].rank(method="first"), 3, labels=["low", "mid", "high"])
    for t in ["low", "mid", "high"]:
        sub = R[R.pp_t == t]
        rng = f"[{sub['dist_pp'].min():.0f}–{sub['dist_pp'].max():.0f}pt]"
        _print(f"    {t:<4} |pin−POC| {rng:<14} {_rate(sub)}")
    ic = R[["dist_pp"]].assign(t=(R.label=="TRUE").astype(int)).corr(method="spearman").iloc[0,1]
    _print(f"    Spearman IC(|pin−POC|, TRUE) = {ic:+.2f}\n")

    # (D) double distribution
    _print("(D) Double-distribution morning → more effective?")
    _print(f"    double-dist (≥2 modes): {_rate(R[R.double_dist])}")
    _print(f"    single/spread         : {_rate(R[~R.double_dist])}")
    a, b = R[R.double_dist], R[~R.double_dist]
    _print(f"    → TRUE-GB lift double vs single: {100*((a.label=='TRUE').mean()-(b.label=='TRUE').mean()):+.0f} pp\n")

    # (E) the combined setup
    setup = R[R.pin_out_va & (R.dist_pp >= R.dist_pp.median()) & R.double_dist]
    rest = R.drop(setup.index)
    _print("(E) COMBINED setup — pin OUTSIDE VA · |pin−POC|≥median · double-dist:")
    _print(f"    setup    : {_rate(setup)}")
    _print(f"    everything else: {_rate(rest)}")
    if len(setup):
        k = int((setup.label=="TRUE").sum())
        _print(f"    → setup TRUE-GB {100*(setup.label=='TRUE').mean():.0f}% vs base {100*base:.0f}%  "
               f"(setup n={len(setup)}, p vs base={_binom_p(k,len(setup),base):.2f})")

    _print("\n  Read: a claim SURVIVES if its 'effective' cell has a clearly higher TRUE-GB rate AND "
           "bigger aft-move than its opposite, beyond the base rate and with enough n. Small per-cell n "
           "and overlapping construction (all three features co-move on a trend day) mean treat a lift "
           "as a mild, consistent tendency — the #27 persistence story — not a standalone edge. "
           "Sign-free; MID move; decision-support, not a trade rule.")


if __name__ == "__main__":
    main()
