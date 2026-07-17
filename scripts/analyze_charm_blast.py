"""Charm vs Gamma-blast: does dealer-charm flow predict blast DIRECTION / continuation?

Charm (∂delta/∂time) is the one greek with a directional mechanism (forced expiry-day re-hedging
drift) and it's near-expiry-dominated — the candidate for the thing the blast score can't do:
DIRECTION. Reads the leak-free crossing dataset (build_blast_dataset.py, now with signed `charm` and
`charm_flow` at the obs window). Tests, point-in-time:
  1. signed charm / charm_flow → raw forward return (does charm call market direction?)
  2. charm PROJECTED on the crossing direction (charm·dirs) → continuation (do charm-supported
     crossings continue more?) — with a momentum control and by DTE.
  3. on TRUE blasts (big continuation), does charm's sign ALIGN with the crossing direction > 50%?

  python scripts/analyze_charm_blast.py            # NIFTY  [store]

⚠️ Sign-dependent (dealer-sign landmine) → read |IC| + alignment; overlapping; exploratory."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    import numpy as np
    import pandas as pd

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    df = pd.read_parquet(ROOT / "data" / "journal" / "blast_ml" / f"{store}.parquet")
    df = df.dropna(subset=["charm", "cont"]).copy()
    df["dirs"] = 2 * df["dir"] - 1                          # +1 up-cross, −1 down-cross
    df["raw_fret"] = df["cont"] * df["dirs"]               # actual signed market fwd return %
    df["raw_mom"] = df["mom_obs"] * df["dirs"]             # actual signed obs-window move
    df["charm_proj"] = df["charm"] * df["dirs"]            # charm projected on the crossing direction
    df["charmflow_proj"] = df["charm_flow"] * df["dirs"]
    df["big"] = df["cont"] > 0.15

    def ic(a, b, sub=None):
        d = (sub if sub is not None else df)[[a, b]].dropna()
        return d[a].corr(d[b], method="spearman") if len(d) >= 200 else float("nan")

    def partial(x, y, z, sub):
        d = sub[[x, y, z]].dropna()
        if len(d) < 300:
            return float("nan")
        r = d.rank()
        A = np.c_[np.ones(len(d)), r[z]]
        ex = r[x] - A @ np.linalg.lstsq(A, r[x], rcond=None)[0]
        ey = r[y] - A @ np.linalg.lstsq(A, r[y], rcond=None)[0]
        return float(pd.Series(ex).corr(pd.Series(ey)))

    _print(f"Charm vs gamma-blast · {store} · {len(df)} crossings ({df['day'].nunique()} days)\n")

    _print("1) Does signed charm / charm-flow predict market DIRECTION (raw forward return)?")
    _print(f"   IC(charm , raw_fret)      = {ic('charm','raw_fret'):+.3f}")
    _print(f"   IC(charm_flow , raw_fret) = {ic('charm_flow','raw_fret'):+.3f}")
    _print(f"   (momentum benchmark) IC(raw_mom , raw_fret) = {ic('raw_mom','raw_fret'):+.3f}")

    _print("\n2) Does charm PROJECTED on the crossing direction predict CONTINUATION?  (by DTE)")
    _print(f"   {'DTE band':<12} {'IC charm·dir→cont':>18} {'IC flow·dir→cont':>18} "
           f"{'partial|mom':>12}   n")
    for lbl, m in [("ALL", df["dte"] <= 99), ("≤1.5d (0-DTE)", df["dte"] <= 1.5),
                   ("1.5–2.5d", df["dte"] > 1.5)]:
        sub = df[m]
        cp = ic("charm_proj", "cont", sub)
        fp = ic("charmflow_proj", "cont", sub)
        pp = partial("charm_proj", "cont", "raw_mom", sub)
        _print(f"   {lbl:<12} {cp:>+18.3f} {fp:>+18.3f} {pp:>+12.3f}   {len(sub):>6,}")

    _print("\n3) On TRUE blasts (cont>0.15%), does charm's SIGN align with the crossing direction?")
    for lbl, m in [("all", df["big"]), ("≤1.5d", df["big"] & (df["dte"] <= 1.5))]:
        sub = df[m]
        align = (np.sign(sub["charm"]) == sub["dirs"]).mean() * 100
        flow_align = (np.sign(sub["charm_flow"]) == sub["dirs"]).mean() * 100
        _print(f"   {lbl:<6}: charm-sign aligns {align:.0f}% · charm-flow aligns {flow_align:.0f}%  "
               f"(50% = no info, n={len(sub):,})")

    # Regime split: charm→continuation on 0-DTE crossings by expiry-STRUCTURE era (pooling fix).
    from app.expiry_regime import ERA_LABELS, era_order, expiry_era
    df["era"] = df["day"].map(expiry_era)
    exp = df[df["dte"] <= 1.5]
    _print("\n4) 0-DTE charm·dir → continuation by expiry-STRUCTURE era (is any edge era-specific?)")
    _print(f"   {'era':<42} {'IC charm·dir→cont':>18} {'partial|mom':>12} {'align%':>7}   n")
    for e in era_order(exp["era"].unique()):
        sub = exp[exp["era"] == e]
        cp = ic("charm_proj", "cont", sub)
        pp = partial("charm_proj", "cont", "raw_mom", sub)
        big = sub[sub["big"]]
        align = (np.sign(big["charm"]) == big["dirs"]).mean() * 100 if len(big) else float("nan")
        _print(f"   {ERA_LABELS[e]:<42} {cp:>+18.3f} {pp:>+12.3f} {align:>6.0f}%   {len(sub):>5,}")

    _print("\n  A real edge = charm·dir→cont clearly > +0.05 (esp 0-DTE) AND survives the momentum "
           "partial AND true-blast alignment > ~55% — and (era split) doesn't only appear in the "
           "pre-Nov24 multi-weekly era. Otherwise charm doesn't call blast direction.")
    _print("  ⚠️ Sign-dependent; overlapping windows; exploratory.")


if __name__ == "__main__":
    main()
