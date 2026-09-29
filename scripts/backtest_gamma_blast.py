"""[B1] Gamma-blast BACKTEST around the 1-min entry model.  [docs/TODO.md B1]

Trades the sign-free 1-min entry model the timing study found (analyze_blast_timing.py, docs §7d):
at each 0-DTE near-money crossing, watch the blast-score trajectory minute-by-minute and ENTER the
winning (rising) leg the moment the score first reaches TRUE within ≤MAX_WAIT min (a "fast TRUE");
skip otherwise. The rising leg IS the direction (sign-free). Exit = stop / target / time.

Because the payoff is lottery-shaped, we report the FULL distribution, not the mean: win rate,
avg-win / avg-loss payoff ratio, P(target), P(stop), and per-trade expectancy — swept over a
SPREAD/slippage cost (the thing that kills naive blast backtests; expiry-afternoon spreads blow out
exactly when premiums explode), compared to a naive baseline (enter every crossing), and split by
expiry-structure era. IV crush needs no separate model: we use the actual traded 1-min premium path,
so a leg that deflates shows it. Close-to-close exits (no intrabar path assumption). Decision-support
research only — NOT a live trade rule, NOT advice.

  python scripts/backtest_gamma_blast.py            # NIFTY  [target_mult=1.5]  [stop=0.30]

⚠️ Mid-close fills + a modelled spread (no real book); first-TRUE trigger; sign-free; exploratory."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))            # for the _blast1min helper

MAX_WAIT = 5          # enter on the first TRUE within 5 min of the cross (the sweet spot, §7d)
ENTER_SCORE = 60      # TRUE threshold
HOLD = 15             # max hold (min) after entry
PREM_FLOOR = 2.0      # min entry premium (pts) — screen penny options
BASE_ENTRY = 3        # baseline rule: enter every crossing at +3 min, no score filter
SPREADS = [0.0, 0.03, 0.06]     # HALF-spread each side (0 = mid/upper-bound, 3%, 6%)


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _simulate(itm_l, e, n, hold, stop, target):
    """Close-to-close trade on the winning leg from entry index e. Returns exit multiple
    (exit/entry), the reason, and MFE (max favourable multiple). None if entry unusable."""
    p0 = itm_l[e]
    if not (p0 == p0 and p0 >= PREM_FLOOR):
        return None
    mfe, last, exit_mult, reason = 1.0, 1.0, None, "time"
    for t in range(e + 1, min(e + hold, n - 1) + 1):
        pt = itm_l[t]
        if pt != pt:
            continue
        last = pt / p0
        mfe = max(mfe, last)
        if pt <= p0 * (1 - stop):
            exit_mult, reason = last, "stop"
            break
        if pt >= p0 * target:
            exit_mult, reason = target, "target"
            break
    if exit_mult is None:
        exit_mult = last
    return {"exit_mult": exit_mult, "reason": reason, "mfe": mfe}


def _day_trades(store, day, stop, target):
    from _blast1min import crossings, first_true, load_day, trajectory_scores, winning_leg
    d = load_day(store, day)
    if d is None or d["n"] < MAX_WAIT + HOLD + 5:
        return []
    n = d["n"]
    out = []
    for cr in crossings(d, MAX_WAIT + HOLD):
        itm_l = winning_leg(d, cr)
        scores = trajectory_scores(d, cr, MAX_WAIT)
        m_enter = first_true(scores, MAX_WAIT, ENTER_SCORE)     # MODEL: first TRUE within MAX_WAIT
        if m_enter is not None:
            sim = _simulate(itm_l, cr["i"] + m_enter, n, HOLD, stop, target)
            if sim:
                out.append({"rule": "model", "day": day, "tod": cr["tod"], **sim})
        base = _simulate(itm_l, cr["i"] + BASE_ENTRY, n, HOLD, stop, target)   # BASELINE: every cross
        if base:
            out.append({"rule": "base", "day": day, "tod": cr["tod"], **base})
    return out


def _stats(df, hs):
    """Per-trade distribution at a given half-spread. ret = exit_mult·(1−hs)/(1+hs) − 1."""
    r = df["exit_mult"] * (1 - hs) / (1 + hs) - 1.0
    win = r > 0
    aw = r[win].mean() if win.any() else float("nan")
    al = r[~win].mean() if (~win).any() else float("nan")
    payoff = (aw / abs(al)) if (al == al and al != 0) else float("nan")
    return {"n": len(r), "win": 100 * win.mean(), "mean": 100 * r.mean(),
            "med": 100 * r.median(), "aw": 100 * aw, "al": 100 * al, "payoff": payoff,
            "ptar": 100 * (df["reason"] == "target").mean(),
            "pstop": 100 * (df["reason"] == "stop").mean()}


def main() -> None:
    import pandas as pd
    from app.expiry_regime import ERA_LABELS, era_order, expiry_era
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    target = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
    stop = float(sys.argv[3]) if len(sys.argv) > 3 else 0.30
    feat = load_features(store)
    if feat is None:
        _print("No feature cache."); return
    dte_by_day = feat.groupby("day")["dte"].first().to_dict()
    days = sorted(d for d, v in dte_by_day.items() if v <= 1.5)
    _print(f"[B1] Gamma-blast backtest · {store} · {len(days)} 0-DTE days · entry: first TRUE ≤{MAX_WAIT}m "
           f"· exit: +{int((target-1)*100)}% target / −{int(stop*100)}% stop / {HOLD}m time\n")
    trades = []
    for di, d in enumerate(days, 1):
        trades.extend(_day_trades(store, d, stop, target))
        if di % 80 == 0:
            _print(f"  …{di}/{len(days)} days")
    tr = pd.DataFrame(trades)
    if tr.empty:
        _print("No trades."); return
    tr["era"] = tr["day"].map(expiry_era)
    model = tr[tr["rule"] == "model"]
    base = tr[tr["rule"] == "base"]
    _print(f"\ntrades — MODEL (fast-TRUE): {len(model):,} over {model['day'].nunique()} days "
           f"(~{len(model)/max(1,len(days)):.2f}/day)  ·  BASELINE (every crossing): {len(base):,}\n")

    _print("(1) Per-trade distribution vs SPREAD (half-spread each side). expectancy = mean return/trade:")
    _print(f"  {'rule / spread':<20} {'n':>5} {'win%':>5} {'exp%':>6} {'med%':>6} {'avgWin%':>8} "
           f"{'avgLoss%':>9} {'payoff':>7} {'tgt%':>5} {'stop%':>6}")
    for lbl, sub in [("MODEL", model), ("BASELINE", base)]:
        for hs in SPREADS:
            s = _stats(sub, hs)
            _print(f"  {lbl+' @'+format(int(hs*100))+'%':<20} {s['n']:>5} {s['win']:>4.0f}% "
                   f"{s['mean']:>+5.1f}% {s['med']:>+5.1f}% {s['aw']:>+7.1f}% {s['al']:>+8.1f}% "
                   f"{s['payoff']:>6.2f}× {s['ptar']:>4.0f}% {s['pstop']:>5.0f}%")
        _print("")

    _print("(2) MODEL by expiry-structure era (at 3% half-spread) — does it survive to the current regime?")
    _print(f"    {'era':<42} {'n':>5} {'win%':>5} {'exp%':>7} {'tgt%':>5}")
    for e in era_order(model["era"].unique()):
        sub = model[model["era"] == e]
        s = _stats(sub, 0.03)
        _print(f"    {ERA_LABELS[e]:<42} {s['n']:>5} {s['win']:>4.0f}% {s['mean']:>+6.1f}% {s['ptar']:>4.0f}%")

    from app.gammablast import afternoon_cut
    cut_m, cut_lbl = afternoon_cut()
    _print(f"\n(3) MODEL, afternoon only (≥{cut_lbl}, at 3% half-spread):")
    aft = model[model["tod"] >= cut_m]
    s = _stats(aft, 0.03)
    _print(f"    n={s['n']}  win {s['win']:.0f}%  expectancy {s['mean']:+.1f}%/trade  "
           f"target-hit {s['ptar']:.0f}%  stopped {s['pstop']:.0f}%  payoff {s['payoff']:.2f}×")

    _print("\n  Read: a real edge ⇒ MODEL expectancy stays POSITIVE after a realistic spread (3-6%) AND "
           "beats BASELINE AND survives into Sep25+. The payoff ratio + target/stop split show the "
           "lottery shape (few big wins vs many small stops). ⚠️ Mid-close fills + modelled spread (no "
           "real book — true expiry-afternoon fills are worse); first-TRUE trigger; close-to-close "
           "exits; sign-free. Decision-support only, not a live rule.")


if __name__ == "__main__":
    main()
