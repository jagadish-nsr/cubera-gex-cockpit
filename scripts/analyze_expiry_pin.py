"""Study #26 — the expiry pin: does spot rally toward it / close on it in the final hour?

Folk claim: on expiry day the underlying is drawn to the "pin" (max-pain / peak-OI strike) as
0-DTE dealer hedging of concentrated OI overwhelms flow, so the last hour drifts toward the pin
and the close lands on it. This is the SIGN-FREE pinning thesis (Ni-Pearson-Poteshman), not the
dealer-gamma-sign landmine — it's about OI concentration, not the ± convention.

Design (as the user framed it):
  • LOCK the pin at T-60 (the 14:30 snapshot) — computed once from that snapshot's OI and frozen.
    Recomputing the pin every minute would let it chase spot and manufacture a fake "pin", so we
    freeze it and ask whether PRICE came to a FIXED level.
  • Then look only at the last 60 minutes of price. Two questions:
      (A) RALLY toward the pin — in the final 30 min (15:00 -> close) did spot move toward the
          locked pin?  (sign(close - spot@15:00) == sign(pin - spot@15:00), among days where the
          pin was not already on top of spot at 15:00).
      (B) CLOSE near the pin — |close - pin| within 1/2 strike (25), 1 strike (50), 0.10%, 0.25%.

Trivial-anchor controls (a pin that just sits where price already is proves nothing):
  • persistence: |close - spot@14:30|.  The pin has edge over "it closed where it was at 14:30"
    only if |close - pin| is SMALLER than |close - spot@14:30|, i.e. the hour bent toward the pin.
  • convergence: did |close - pin| < |spot@14:30 - pin|  (distance to the FIXED pin shrank).

Two pin definitions reported side by side: MAX-PAIN (holder-payout min) and PEAK-OI (single most
crowded strike). Both from the frozen 14:30 snapshot.

  python scripts/analyze_expiry_pin.py            # NIFTY, last 30 expiries
  python scripts/analyze_expiry_pin.py NIFTY 30

Data: data/journal/dhan_intraday/<store>/<day>.parquet (Dhan 1-min per-strike OI; OI in SHARES,
but max-pain/peak-OI are scale-free in OI so the lot-size unit is irrelevant here). Expiry days =
open dte < 0.6 from the intraday feature cache. Sign-free, decision-support only, NOT a trade rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCK_T = "14:30"          # T-60: freeze the pin here
LAST30_T = "15:00"        # start of the final 30 minutes
STRIKE = 50.0             # NIFTY strike spacing


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _snap_at(df, hhmm):
    """Rows at the last stamp <= hhmm (nearest prior minute if the exact one is missing)."""
    ts = sorted(t for t in df["time_ist"].unique() if t <= hhmm)
    if not ts:
        return None
    return df[df["time_ist"] == ts[-1]]


def _spot_at(df, hhmm):
    s = _snap_at(df, hhmm)
    return None if s is None or s.empty else float(s["spot"].iloc[0])


def _max_pain(snap):
    """Strike minimising total ITM payout to option holders (sign-free). O(n^2), n~25."""
    coi = snap[snap["side"] == "CE"].groupby("strike")["oi"].sum()
    poi = snap[snap["side"] == "PE"].groupby("strike")["oi"].sum()
    strikes = sorted(set(coi.index) | set(poi.index))
    best_k = best_pain = None
    for kexp in strikes:
        pain = 0.0
        for k in strikes:
            if k < kexp:
                pain += float(coi.get(k, 0.0)) * (kexp - k)   # calls ITM above them
            elif k > kexp:
                pain += float(poi.get(k, 0.0)) * (k - kexp)   # puts ITM below them
        if best_pain is None or pain < best_pain:
            best_pain, best_k = pain, kexp
    return best_k


def _peak_oi(snap):
    """Single strike with the most stacked OI (call+put)."""
    tot = snap.groupby("strike")["oi"].sum()
    return float(tot.idxmax()) if len(tot) else None


def _sign(x, eps=0.0):
    return 0 if abs(x) <= eps else (1 if x > 0 else -1)


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.expiry_regime import expiry_era
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    n_exp = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    lock_t = sys.argv[3] if len(sys.argv) > 3 else LOCK_T

    feat = load_features(store)
    if feat is None:
        _print("No feature cache — build it first (app.intraday_features.build)."); return
    dte0 = feat.groupby("day")["dte"].first()
    exp_days = sorted(d for d, v in dte0.items() if float(v) < 0.6)[-n_exp:]

    raw_dir = ROOT / "data" / "journal" / "dhan_intraday" / store
    rows = []
    for d in exp_days:
        p = raw_dir / f"{d}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p, columns=["time_ist", "side", "strike", "spot", "oi"])
        lock = _snap_at(df, lock_t)
        if lock is None or lock.empty:
            continue
        spot_lock = float(lock["spot"].iloc[0])
        spot_15 = _spot_at(df, LAST30_T)
        # close = the last available minute (Dhan store ends ~15:29)
        last_t = max(df["time_ist"].unique())
        close = _spot_at(df, last_t)
        if spot_15 is None or close is None:
            continue
        rows.append({
            "day": d, "era": expiry_era(d),
            "spot_lock": spot_lock, "spot_15": spot_15, "close": close,
            "pin_mp": _max_pain(lock), "pin_oi": _peak_oi(lock),
        })

    if not rows:
        _print("No expiry days with a 14:30 snapshot found."); return
    R = pd.DataFrame(rows)
    _print(f"Study #26 · expiry pin · {store} · {len(R)} expiries "
           f"({R['day'].iloc[0]} → {R['day'].iloc[-1]}) · pin LOCKED at {lock_t}, "
           f"last-30 direction from {LAST30_T}\n")

    for pin_col, name in [("pin_mp", "MAX-PAIN"), ("pin_oi", "PEAK-OI")]:
        pin = R[pin_col].astype(float)
        gap15 = pin - R["spot_15"]                 # where the pin sits vs 15:00 spot
        move30 = R["close"] - R["spot_15"]         # final-30-min move
        d_close = (R["close"] - pin).abs()         # |close - pin|
        d_persist = (R["close"] - R["spot_lock"]).abs()   # trivial anchor: where it was at 14:30
        d_start = (R["spot_lock"] - pin).abs()     # distance to the FIXED pin at lock time

        # (A) rally toward the pin in the last 30 min — only where the pin wasn't already on spot
        live = gap15.abs() >= STRIKE / 2           # ≥ half a strike of room to move
        toward = ((np.sign(move30) == np.sign(gap15)) & live)
        n_live = int(live.sum())
        _print(f"── pin = {name} ──")
        _print(f"(A) RALLY toward pin, last 30 min (15:00→close), among {n_live}/{len(R)} days with "
               f"≥{STRIKE/2:.0f}pt room:")
        if n_live:
            k = int(toward.sum())
            # two-sided binomial tail vs a fair coin — is 'toward' more than chance?
            from math import comb
            pmf = [comb(n_live, i) * 0.5 ** n_live for i in range(n_live + 1)]
            pv = sum(p for i, p in enumerate(pmf) if p <= pmf[k] + 1e-12)
            _print(f"      moved TOWARD pin: {k}/{n_live}  ({100*k/n_live:.0f}%)  "
                   f"[coin-flip null = 50%, two-sided p = {pv:.2f}]")

        # (B) close near the pin
        _print("(B) CLOSE near pin:")
        for thr, lbl in [(STRIKE/2, "≤25 pt (½ strike)"), (STRIKE, "≤50 pt (1 strike)"),
                         (0.0010, "≤0.10%"), (0.0025, "≤0.25%")]:
            hit = (d_close <= thr) if thr >= 1 else (d_close <= thr * R["close"])
            _print(f"      {lbl:<18} {int(hit.sum()):>2}/{len(R)}  ({100*hit.mean():.0f}%)")

        # convergence vs trivial anchors
        conv = (d_close < d_start).mean()
        beats_persist = (d_close < d_persist).mean()
        _print(f"    median |spot@{lock_t} − pin| (how close it already was at lock) = {d_start.median():.0f} pt")
        _print(f"    median |close−pin| = {d_close.median():.0f} pt   vs   median |close−spot@{lock_t}| "
               f"(persistence) = {d_persist.median():.0f} pt")
        _print(f"    converged (|close−pin| < |{lock_t}spot−pin|): {100*conv:.0f}% of days  ·  "
               f"pin beats persistence (closer than {lock_t} spot): {100*beats_persist:.0f}%")
        _print("")

    # honest read
    _print("Read: the pin has edge only if the close lands NEARER the frozen pin than it does to the"
           " 14:30 spot (persistence). Convergence % near 50 and 'beats persistence' near 50 = the"
           " pin is not pulling; the hour just meandered. Sign-free, decision-support, not a trade rule.")


if __name__ == "__main__":
    main()
