"""Backtest the DEFINED-RISK alternative to the wall short-strangle: a SHORT IRON FLY at the pin.

Motivation (dashboard): when the call wall and put wall COINCIDE on one strike, the "sell strangle
at the walls" play degenerates into a naked straddle — a zero-width credit band and no valid
wall-stop. The defined-risk expression is a short iron fly: SELL the pin straddle + BUY protective
wings W strikes out each side. The wings ARE the stop (max loss is capped), so — unlike the naked
strangle (which we stop on a wall breach) — the iron fly is simply HELD to the 15:25 close. This
tests whether capping the tail with bought wings keeps the calm short-premium edge or bleeds it away
in theta paid for the wings.

  entry 09:20 IST, CALM (|GEX| top tercile) · body = pin (magnet→nearest listed strike, else ATM)
  legs  SELL body CE + body PE ,  BUY (body+W·step) CE + (body−W·step) PE      [W swept]
  credit = (cBody−cWing_call) + (pBody−pWing_put) ;  max risk = W·step − credit  (defined)
  exit   MTM at 15:25 (no stop — the wings define the risk) ;  P&L = credit − cost-to-close
  return = P&L as % of MAX RISK (margin/ROI basis — the right denominator for a defined-risk trade;
           NOT % of credit like the naked strangle, so the two numbers are not directly comparable)

Payoff is bounded [−max_risk, +credit], so we report the full distribution: win%, avg-win/avg-loss,
P(near-max-loss), and per-trade expectancy — swept over a modelled spread AND the wing width, split by
expiry-structure era. 1-min premiums (Dhan store) so IV crush + the intraday path are real.

  python scripts/backtest_iron_fly.py            # NIFTY  [entry=09:20]  [exit=15:25]

⚠️ Nearest-listed-strike premiums (body+both wings must be in coverage), mid-close fills + a modelled
per-leg spread, MTM-at-close (not held to expiry on non-expiry days); sign-free; exploratory. NOT a
live rule. Compare to scripts/backtest_wall_strangle.py (the naked-strangle baseline)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))            # for the _blast1min helper

SPREADS = [0.0, 0.02, 0.04]      # HALF-spread each side (0 = mid), applied per leg (4 legs) at entry+exit
WINGS = [2, 3, 4]                # wing distance in STRIKES (W·step pts each side); swept
MIN_PREM = 2.0                   # each SOLD (body) leg's entry premium must be ≥ this (pts)
REF_SPREAD = 0.02                # half-spread used for the era table
REF_WING = 3                     # wing width used for the era table


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _idx_at(stamps, hhmm):
    from _blast1min import mins
    want = mins(hhmm)
    best, bd = None, 1e9
    for i, t in enumerate(stamps):
        d = abs(mins(t) - want)
        if d < bd:
            best, bd = i, d
    return best if bd <= 3 else None       # within 3 min of the target


def _prem(arr_map, strike, i):
    a = arr_map.get(strike)
    if a is None:
        return None
    v = a[i]
    return float(v) if v == v else None


def _day_trade(store, day, body_target, wing, entry, exit_):
    """One iron-fly trade for a day at a given wing width, or None if unpriceable."""
    from _blast1min import load_day
    d = load_day(store, day)
    if d is None:
        return None
    ei, xi = _idx_at(d["stamps"], entry), _idx_at(d["stamps"], exit_)
    if ei is None or xi is None or xi <= ei:
        return None
    strikes, step, spot = d["strikes"], d["step"], d["spot"]
    body = min(strikes, key=lambda k: abs(k - body_target))
    kc_w, kp_w = body + wing * step, body - wing * step
    if kc_w not in d["CL"] or kp_w not in d["PL"]:      # wings must be in the store's strike coverage
        return None
    cB, pB = _prem(d["CL"], body, ei), _prem(d["PL"], body, ei)
    cW, pW = _prem(d["CL"], kc_w, ei), _prem(d["PL"], kp_w, ei)
    if None in (cB, pB, cW, pW) or cB < MIN_PREM or pB < MIN_PREM:
        return None
    credit = (cB + pB) - (cW + pW)                       # net credit collected (pts)
    width = wing * step
    max_risk = width - credit                            # defined max loss (pts); guard below
    if credit <= 0 or max_risk <= 0:
        return None                                       # no credit, or credit ≥ width (mispriced)
    cBx, pBx = _prem(d["CL"], body, xi), _prem(d["PL"], body, xi)
    cWx, pWx = _prem(d["CL"], kc_w, xi), _prem(d["PL"], kp_w, xi)
    if None in (cBx, pBx, cWx, pWx):
        return None
    exit_val = (cBx + pBx) - (cWx + pWx)                 # cost to close the fly at 15:25
    return {"day": day, "credit": credit, "exit_val": exit_val, "max_risk": max_risk,
            "width": width,
            "gross_entry": cB + pB + cW + pW,            # Σ|leg premium| — the real spread base …
            "gross_exit": cBx + pBx + cWx + pWx}         # … at entry and at exit (all 4 legs)


def _stats(df, hs):
    """Per-trade distribution at a half-spread ``hs`` (applied per leg: sell legs receive less, buy
    legs cost more, at entry and exit). return% = P&L as % of MAX RISK (ROI on margin). The spread is
    charged on the ACTUAL four-leg premiums crossed at entry AND exit — hs · Σ|leg premium| each — the
    honest cost of a marketable round-trip through the fly's own bid/ask (all four legs both times)."""
    fric = hs * (df["gross_entry"] + df["gross_exit"])       # entry cross + exit cross, all 4 legs
    pnl = df["credit"] - df["exit_val"] - fric
    r = pnl / df["max_risk"] * 100.0
    win = r > 0
    aw = r[win].mean() if win.any() else float("nan")
    al = r[~win].mean() if (~win).any() else float("nan")
    near_max = 100.0 * (r <= -80.0).mean()                   # share within 80% of the defined max loss
    return {"n": len(r), "win": 100 * win.mean(), "exp": r.mean(), "med": r.median(),
            "aw": aw, "al": al, "payoff": (aw / abs(al)) if (al == al and al) else float("nan"),
            "pmaxl": near_max}


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.expiry_regime import ERA_LABELS, era_order, expiry_era
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    entry = sys.argv[2] if len(sys.argv) > 2 else "09:20"
    exit_ = sys.argv[3] if len(sys.argv) > 3 else "15:25"
    feat = load_features(store)
    if feat is None:
        _print("No feature cache."); return
    feat = feat.copy()
    feat["absg"] = feat["net_gex"].abs()
    e = feat[feat["time_ist"] == entry].drop_duplicates("day").set_index("day")
    if e.empty:
        e = feat.sort_values("time_ist").groupby("day").first()
    q2 = e["absg"].quantile(2 / 3)                            # top tercile = calm / pinning
    days = sorted(e.index)
    _print(f"Iron-fly backtest · {store} · {len(days)} days · entry {entry} → exit {exit_} "
           f"· body = pin (magnet→nearest strike) · HELD to close (wings = the stop) · "
           f"CALM = |GEX| top tercile\n")

    rows = []
    for di, day in enumerate(days, 1):
        row = e.loc[day]
        body_target = float(row["magnet"]) if ("magnet" in e.columns and row["magnet"] == row["magnet"]) \
            else float(row["spot"])
        for w in WINGS:
            r = _day_trade(store, day, body_target, w, entry, exit_)
            if r:
                r["wing"] = w
                r["calm"] = bool(row["absg"] >= q2)
                r["era"] = expiry_era(day)
                rows.append(r)
        if di % 200 == 0:
            _print(f"  …{di}/{len(days)} days")
    tr = pd.DataFrame(rows)
    if tr.empty:
        _print("No priceable iron flies (wings outside strike coverage?)."); return
    _print(f"priced flies: {len(tr):,} (day×wing) · {tr['day'].nunique():,} days · "
           f"ret = P&L as % of DEFINED MAX RISK (ROI)\n")

    # (1) per-trade distribution vs SPREAD, per wing width — CALM (the play) vs ALL (baseline)
    _print("(1) Per-trade distribution by WING width & SPREAD (half-spread/leg). expectancy = mean ROI/trade:")
    _print(f"  {'set · wing · spread':<26} {'n':>5} {'win%':>5} {'exp%':>6} {'med%':>6} {'avgWin%':>8} "
           f"{'avgLoss%':>9} {'payoff':>7} {'nearMaxL%':>9}")
    for w in WINGS:
        sw = tr[tr["wing"] == w]
        for lbl, sub in [("CALM", sw[sw["calm"]]), ("ALL", sw)]:
            for hs in SPREADS:
                if len(sub) < 30:
                    continue
                s = _stats(sub, hs)
                tag = f"{lbl} w{w} @{int(hs*100)}%"
                _print(f"  {tag:<26} {s['n']:>5} {s['win']:>4.0f}% {s['exp']:>+5.1f}% {s['med']:>+5.1f}% "
                       f"{s['aw']:>+7.1f}% {s['al']:>+8.1f}% {s['payoff']:>6.2f}× {s['pmaxl']:>8.0f}%")
        _print("")

    # (2) CALM by expiry-structure era (at REF wing + REF spread)
    _print(f"(2) CALM iron fly by expiry-structure era (wing {REF_WING}, {int(REF_SPREAD*100)}% half-spread):")
    _print(f"    {'era':<42} {'n':>5} {'win%':>5} {'exp%':>7} {'nearMaxL%':>10}")
    cw = tr[(tr["wing"] == REF_WING) & (tr["calm"])]
    for era in era_order(cw["era"].unique()):
        s = _stats(cw[cw["era"] == era], REF_SPREAD)
        _print(f"    {ERA_LABELS[era]:<42} {s['n']:>5} {s['win']:>4.0f}% {s['exp']:>+6.1f}% {s['pmaxl']:>9.0f}%")

    _print("\n  Read: a real defined-risk edge ⇒ CALM expectancy (ROI on max-risk) stays POSITIVE after a "
           "realistic per-leg spread AND beats the ALL baseline AND survives into Sep25+. Compare to the "
           "naked wall-strangle (backtest_wall_strangle.py): the fly trades a capped tail (bounded loss, "
           "no stop-out) for a smaller credit — the question is whether the wings cost more theta than the "
           "tail they remove. Denominators differ (fly = % of max risk; strangle = % of credit) — read the "
           "SIGN and the era-survival, not a head-to-head number. ⚠️ nearest-strike premiums, modelled "
           "per-leg spread, MTM-at-close; sign-free.")


if __name__ == "__main__":
    main()
