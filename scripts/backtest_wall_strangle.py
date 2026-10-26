"""Backtest the dashboard's calm-regime play: SHORT STRANGLE at the walls, stop on a wall breach.

The "sell premium near the walls" suggestion as a testable rule:
at 09:20 IST, in the CALM (high-|GEX| / pinning) regime, SELL the call at the call-wall and the put
at the put-wall (nearest listed strikes). STOP if spot breaches either wall intraday (the short leg
goes ITM); else exit at the close. The edge, if any, is the VOL/short-premium premium in calm — NOT
the walls containing price (docs §4d showed they barely do), so we test it honestly against a
no-filter baseline and split by expiry-structure era.

Payoff is lottery-shaped (many small credit-keeps vs occasional stop-outs), so we report the FULL
distribution: win%, avg-win / avg-loss, payoff ratio, P(stopped), and per-trade expectancy — swept
over a spread cost. Return is P&L as a % of the credit collected (short-strangle gains cap ~+100%,
losses can exceed it). 1-min premiums (Dhan store) so IV crush + the intraday path are real.

  python scripts/backtest_wall_strangle.py            # NIFTY  [entry=09:20]  [exit=15:25]

⚠️ Nearest-strike premiums (walls must be in the store's strike coverage), mid-close fills + a
modelled spread (no real book), close-to-close stop check; sign-free; exploratory. NOT a live rule."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))            # for the _blast1min helper

SPREADS = [0.0, 0.02, 0.04]      # HALF-spread each side (0 = mid), applied to entry credit & exit cost
MIN_PREM = 2.0                   # each wall leg's entry premium must be ≥ this (pts)


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


def _day_trade(store, day, call_wall, put_wall, entry, exit_):
    import numpy as np
    from _blast1min import load_day
    d = load_day(store, day)
    if d is None:
        return None
    ei, xi = _idx_at(d["stamps"], entry), _idx_at(d["stamps"], exit_)
    if ei is None or xi is None or xi <= ei:
        return None
    strikes, step, spot = d["strikes"], d["step"], d["spot"]
    ncall = min(strikes, key=lambda k: abs(k - call_wall))
    nput = min(strikes, key=lambda k: abs(k - put_wall))
    # the wall strikes must actually be in coverage (else premiums are edge/deep-OTM noise)
    if abs(ncall - call_wall) > 1.5 * step or abs(nput - put_wall) > 1.5 * step:
        return None
    cl, pl = d["CL"][ncall], d["PL"][nput]
    ec, ep = cl[ei], pl[ei]
    if not (ec == ec and ep == ep and ec >= MIN_PREM and ep >= MIN_PREM):
        return None
    credit = ec + ep
    stopped, exit_val = False, None
    for t in range(ei + 1, xi + 1):
        s = spot[t]
        if s != s:
            continue
        if s > call_wall or s < put_wall:               # wall breach -> stop
            cv, pv = cl[t], pl[t]
            if cv == cv and pv == pv:
                stopped, exit_val = True, cv + pv
                break
    if exit_val is None:                                 # no breach -> exit at close
        cv, pv = cl[xi], pl[xi]
        if not (cv == cv and pv == pv):
            return None
        exit_val = cv + pv
    return {"day": day, "credit": credit, "exit_val": exit_val, "stopped": stopped}


def _stats(df, hs):
    """Per-trade distribution at a half-spread. ret% = (credit·(1−hs) − exit·(1+hs)) / credit."""
    import numpy as np
    r = (df["credit"] * (1 - hs) - df["exit_val"] * (1 + hs)) / df["credit"] * 100.0
    win = r > 0
    aw = r[win].mean() if win.any() else float("nan")
    al = r[~win].mean() if (~win).any() else float("nan")
    return {"n": len(r), "win": 100 * win.mean(), "exp": r.mean(), "med": r.median(),
            "aw": aw, "al": al, "payoff": (aw / abs(al)) if (al == al and al) else float("nan"),
            "pstop": 100 * df["stopped"].mean()}


def main() -> None:
    import numpy as np
    import pandas as pd
    from app.expiry_regime import ERA_LABELS, era_order, expiry_era
    from app.intraday_features import load_features

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    entry = sys.argv[2] if len(sys.argv) > 2 else "09:20"
    exit_ = sys.argv[3] if len(sys.argv) > 3 else "15:25"
    feat = load_features(store)
    if feat is None or "call_wall" not in feat.columns:
        _print("No levels-enriched feature cache."); return
    # per-day entry snapshot (walls + |GEX| for the calm filter) at the entry stamp
    feat = feat.copy()
    feat["absg"] = feat["net_gex"].abs()
    e = feat[feat["time_ist"] == entry].drop_duplicates("day").set_index("day")
    if e.empty:
        e = feat.sort_values("time_ist").groupby("day").first()   # fallback: first stamp
    q2 = e["absg"].quantile(2 / 3)                                # top tercile = calm/pinning
    days = sorted(e.index)
    _print(f"Wall short-strangle backtest · {store} · {len(days)} days · entry {entry} → exit {exit_} "
           f"· stop on wall breach · CALM = |GEX| top tercile\n")
    rows = []
    for di, day in enumerate(days, 1):
        r = _day_trade(store, day, float(e.loc[day, "call_wall"]), float(e.loc[day, "put_wall"]),
                       entry, exit_)
        if r:
            r["calm"] = bool(e.loc[day, "absg"] >= q2)
            r["era"] = expiry_era(day)
            rows.append(r)
        if di % 200 == 0:
            _print(f"  …{di}/{len(days)} days")
    tr = pd.DataFrame(rows)
    if tr.empty:
        _print("No priceable strangles (walls outside the store's strike coverage?)."); return
    calm, alld = tr[tr["calm"]], tr
    _print(f"\npriced strangles: {len(tr):,} days ({len(calm):,} calm)  ·  "
           f"ret = P&L as % of credit collected\n")

    _print("(1) Per-trade distribution vs SPREAD (half-spread each side). expectancy = mean ret/trade:")
    _print(f"  {'set / spread':<20} {'n':>5} {'win%':>5} {'exp%':>6} {'med%':>6} {'avgWin%':>8} "
           f"{'avgLoss%':>9} {'payoff':>7} {'stop%':>6}")
    for lbl, sub in [("CALM (play)", calm), ("ALL (baseline)", alld)]:
        for hs in SPREADS:
            s = _stats(sub, hs)
            _print(f"  {lbl+' @'+format(int(hs*100))+'%':<20} {s['n']:>5} {s['win']:>4.0f}% "
                   f"{s['exp']:>+5.1f}% {s['med']:>+5.1f}% {s['aw']:>+7.1f}% {s['al']:>+8.1f}% "
                   f"{s['payoff']:>6.2f}× {s['pstop']:>5.0f}%")
        _print("")

    _print("(2) CALM play by expiry-structure era (at 2% half-spread):")
    _print(f"    {'era':<42} {'n':>5} {'win%':>5} {'exp%':>7} {'stop%':>6}")
    for era in era_order(calm["era"].unique()):
        s = _stats(calm[calm["era"] == era], 0.02)
        _print(f"    {ERA_LABELS[era]:<42} {s['n']:>5} {s['win']:>4.0f}% {s['exp']:>+6.1f}% {s['pstop']:>5.0f}%")

    _print("\n  Read: a real edge ⇒ CALM expectancy stays POSITIVE after a realistic spread AND beats the "
           "ALL baseline AND survives into Sep25+. High win% with a poor payoff (few big stop-outs erasing "
           "many small credit-keeps) is the short-premium signature — expectancy, not win%, is the verdict. "
           "⚠️ nearest-strike premiums, mid-close fills + modelled spread, close-to-close stop; sign-free.")


if __name__ == "__main__":
    main()
