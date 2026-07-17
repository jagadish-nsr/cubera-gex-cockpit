"""Speed-of-ignition + grade transitions: does time-to-TRUE (and WEAK→TRUE upgrades) unlock a blast?

The live grade is a single fixed-window (10-min) snapshot, but a crossing's grade EVOLVES
(FAKE→WEAK→TRUE, sometimes TRUE→decay). build_blast_dataset records, per crossing: the static
10-min grade components (cover/thrust/wall → the STATIC grade), the first 5-min step the
EXPANDING-window grade reaches TRUE (`k_true`, ≤30 min else "never"), and the outcome over a FIXED
20-min window AFTER that trigger (`cont_after` spot %, `mult_after_peak` winning-leg premium ×).
Point-in-time: k_true is elapsed at the trigger; the outcome is measured strictly after.

Tests: (1) time-to-TRUE distribution; (2) the FULL post-trigger premium-multiple distribution by
ignition speed (percentiles + P≥1.5×/2×/3×); (3) the value of WAITING — of crossings graded WEAK
at the 10-min snapshot, how many upgrade to TRUE and deliver a good blast (≥1.5×)?; era split.

  python scripts/analyze_blast_speed.py            # NIFTY   [good_mult=1.5]

⚠️ Exploratory, sign-free. Post-trigger multiple is a MID peak (real expiry fills are worse)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_SPEED = [("FAST  (≤10 min)", {1, 2}), ("MID   (15-20 min)", {3, 4}), ("SLOW  (25-30 min)", {5, 6})]
_STATIC = [("TRUE-leaning (break sustaining)", "TRUE (static 10-min)"),
           ("WEAK / unconfirmed", "WEAK (static 10-min)"),
           ("FAKE-leaning (pin/cap)", "FAKE (pin/cap)"),
           ("FAKE-leaning (writers defending K)", "FAKE (defending)")]


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _dist_table(df, title, good):
    """Post-trigger peak-multiple DISTRIBUTION by ignition speed (percentiles + P thresholds)."""
    _print(title)
    _print(f"  {'ignition speed':<18} {'n':>5} | {'medCont':>8} | {'p50':>6} {'p75':>6} {'p90':>6} "
           f"{'p95':>6} | {'≥'+format(good,'g')+'×':>6} {'≥2×':>5} {'≥3×':>5}")
    ever = df[df["k_true"].notna()]
    for label, ks in _SPEED:
        g = ever[ever["k_true"].isin(ks)]
        mp = g["mult_after_peak"].dropna()
        ca = g["cont_after"].dropna()
        if len(mp) == 0:
            _print(f"  {label:<18} {len(g):>5} | (no post-trigger outcome)")
            continue
        q = mp.quantile([.5, .75, .9, .95])
        medc = f"{ca.median():+.3f}%" if len(ca) else "   —"
        _print(f"  {label:<18} {len(g):>5} | {medc:>8} | {q[.5]:>5.2f}× {q[.75]:>5.2f}× "
               f"{q[.9]:>5.2f}× {q[.95]:>5.2f}× | {100*(mp>=good).mean():>5.0f}% "
               f"{100*(mp>=2).mean():>4.0f}% {100*(mp>=3).mean():>4.0f}%")


def _transition(df, title, good):
    """STATIC 10-min grade → does it reach TRUE? → of those, the good-blast (≥good×) rate."""
    _print(title)
    _print(f"  {'static 10-min grade':<22} {'n':>5} {'reach TRUE':>11} | "
           f"{'of-reached medMult':>18} {'≥'+format(good,'g')+'×':>6} {'≥2×':>5}")
    for key, label in _STATIC:
        g = df[df["static"] == key]
        if len(g) == 0:
            continue
        reached = g[g["k_true"].notna()]
        mp = reached["mult_after_peak"].dropna()
        pr = 100.0 * len(reached) / len(g)
        medm = f"{mp.median():.2f}×" if len(mp) else "   —"
        pg = f"{100*(mp>=good).mean():.0f}%" if len(mp) else "  —"
        p2 = f"{100*(mp>=2).mean():.0f}%" if len(mp) else "  —"
        _print(f"  {label:<22} {len(g):>5} {pr:>10.0f}% | {medm:>18} {pg:>6} {p2:>5}")


def main() -> None:
    import pandas as pd
    from app.expiry_regime import ERA_LABELS, era_order, expiry_era
    from app.gammablast import grade_blast

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    good = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5     # "good blast" premium multiple
    path = ROOT / "data" / "journal" / "blast_ml" / f"{store}.parquet"
    if not path.exists():
        _print(f"No {path.name} — build it first (build_blast_dataset.py {store})."); return
    df = pd.read_parquet(path)
    if "k_true" not in df.columns:
        _print("Dataset has no trajectory fields — rebuild (build_blast_dataset.py)."); return
    df["era"] = df["day"].map(expiry_era)
    df["static"] = [grade_blast(c, t, wr, we)["grade"]
                    for c, t, wr, we in zip(df["cover"], df["thrust"],
                                            df["wall_ratio"], df["wall_erode"])]
    ever = df["k_true"].notna()
    aft = df[(df["dte"] <= 1.5) & (df["tod"] >= 13 * 60 + 45)]      # the real blast window

    _print(f"Speed-of-ignition + transitions · {store} · {len(df):,} crossings "
           f"({df['day'].nunique()} days)  ·  good blast = ≥{good:g}× winning-leg peak")
    _print(f"reached TRUE within 30 min: {100*ever.mean():.0f}% (0-DTE afternoon "
           f"{100*aft['k_true'].notna().mean():.0f}%)  ·  median time-to-TRUE: "
           f"{df.loc[ever,'k_true'].median()*5:.0f} min\n")
    _print("Time-to-TRUE distribution (share of ALL crossings):")
    cells = [f"{kk*5}m {100*(df['k_true']==kk).mean():.0f}%" for kk in range(1, 7)]
    cells.append(f"never {100*(~ever).mean():.0f}%")
    _print("  " + "   ".join(cells) + "\n")

    _dist_table(df, "(1) Post-trigger peak-multiple DISTRIBUTION by speed — ALL crossings:", good)
    _print("")
    _dist_table(aft, "(2) same — 0-DTE AFTERNOON (≥13:45), the real blast window:", good)
    _print("")
    _transition(aft, "(3) Value of WAITING — static 10-min grade → reaches TRUE → good blast "
                     "(0-DTE afternoon):", good)

    _print("\n(4) 0-DTE afternoon by expiry-structure era — ever-TRUE% and FAST good-blast rate:")
    _print(f"    {'era':<42} {'ever-TRUE%':>10} {'FAST n':>7} {'FAST ≥'+format(good,'g')+'×':>10}")
    for e in era_order(aft["era"].unique()):
        sub = aft[aft["era"] == e]
        fast = sub[sub["k_true"].isin({1, 2})]
        fmp = fast["mult_after_peak"].dropna()
        pg = f"{100*(fmp>=good).mean():.0f}%" if len(fmp) else "—"
        _print(f"    {ERA_LABELS[e]:<42} {100*sub['k_true'].notna().mean():>9.0f}% "
               f"{len(fast):>7} {pg:>10}")

    _print(f"\n  Read: a speed/transition edge ⇒ FAST ignition (and WEAK→TRUE upgrades) clearly lift the "
           f"≥{good:g}× rate over SLOW / staying-WEAK, AND hold in the current Sep25+ era. If the "
           f"distributions overlap, time-to-TRUE is not adding over the grade. ⚠️ First-TRUE trigger "
           "(grade can reach TRUE then decay); MID-peak multiple (real fills worse); sign-free.")


if __name__ == "__main__":
    main()
