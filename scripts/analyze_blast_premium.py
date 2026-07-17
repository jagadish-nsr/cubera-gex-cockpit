"""How often does each blast GRADE bucket actually deliver a >3× premium jump?

Ties the sign-free blast GRADE (app.gammablast.grade_blast — TRUE / WEAK / FAKE) to the retail
"2–3× in minutes" folklore, on the leak-free crossing dataset (build_blast_dataset.py). For each
near-money crossing we regrade it from its stored point-in-time components (cover, thrust,
wall_ratio, wall_erode) — the SAME grader the live dashboard uses — then measure the winning
(ITM-going) leg's premium multiple over the 40-min payoff window:

  blast_mult_peak = max(winning-leg premium over [entry, +40m]) / entry premium   ← the "spike"
  blast_mult_exit = winning-leg premium at +40m / entry premium                   ← hold-to-exit

and report, per grade bucket, the share of events that hit ≥3× (and ≥2×). Point-in-time: the grade
uses only the observation window; the multiple is measured strictly after entry (io). Sign-free.

  python scripts/analyze_blast_premium.py            # NIFTY  [entry_prem_floor_pts]

⚠️ Exploratory. Peak = an intraday spike at MID (real fills are worse — expiry-afternoon spreads
blow out exactly then); entry_prem floor screens penny options whose multiples are noise. Sign-free."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# grade buckets in the order we report them
_BUCKETS = [
    ("TRUE-leaning (break sustaining)", "TRUE  (break sustaining)"),
    ("WEAK / unconfirmed", "WEAK  (unconfirmed)"),
    ("FAKE-leaning (pin/cap)", "FAKE  (pin / cap)"),
    ("FAKE-leaning (writers defending K)", "FAKE  (writers defending)"),
]


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _table(df, title):
    _print(title)
    _print(f"  {'grade bucket':<27} {'n':>5} {'share':>6} | {'peak≥3×':>8} {'peak≥2×':>8} "
           f"{'exit≥3×':>8} {'exit≥2×':>8} {'medPeak':>8} {'medEntry':>8}")
    tot = len(df)
    for key, label in _BUCKETS:
        g = df[df["grade"] == key]
        if len(g) == 0:
            _print(f"  {label:<27} {0:>5}")
            continue
        share = 100.0 * len(g) / tot if tot else 0.0
        p3 = 100.0 * (g["blast_mult_peak"] >= 3.0).mean()
        p2 = 100.0 * (g["blast_mult_peak"] >= 2.0).mean()
        e3 = 100.0 * (g["blast_mult_exit"] >= 3.0).mean()
        e2 = 100.0 * (g["blast_mult_exit"] >= 2.0).mean()
        mp = g["blast_mult_peak"].median()
        me = g["blast_entry_prem"].median()
        _print(f"  {label:<27} {len(g):>5} {share:>5.0f}% | {p3:>7.0f}% {p2:>7.0f}% "
               f"{e3:>7.0f}% {e2:>7.0f}% {mp:>7.2f}× {me:>7.0f}")


def main() -> None:
    import pandas as pd
    from app.expiry_regime import ERA_LABELS, era_order, expiry_era
    from app.gammablast import grade_blast

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    floor = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0    # entry-premium floor (pts)
    path = ROOT / "data" / "journal" / "blast_ml" / f"{store}.parquet"
    if not path.exists():
        _print(f"No {path.name} — run scripts/build_blast_dataset.py {store} first.")
        return
    df = pd.read_parquet(path)
    need = {"cover", "thrust", "wall_ratio", "wall_erode", "blast_mult_peak", "blast_mult_exit",
            "blast_entry_prem"}
    if not need <= set(df.columns):
        _print("Dataset is missing the premium-multiple columns — rebuild it "
               f"(build_blast_dataset.py {store}).")
        return

    df["grade"] = [grade_blast(c, t, wr, we)["grade"]
                   for c, t, wr, we in zip(df["cover"], df["thrust"],
                                           df["wall_ratio"], df["wall_erode"])]
    raw_n = len(df)
    df = df.dropna(subset=["blast_mult_peak", "blast_mult_exit"])
    # screen penny options (tiny entry premium ⇒ absurd multiples off a 1-tick move)
    kept = df[df["blast_entry_prem"] >= floor].copy()

    _print(f"Blast grade → premium multiple · {store} · {raw_n:,} crossings "
           f"({df['day'].nunique()} days)")
    _print(f"entry-premium floor ≥ {floor:g} pts → {len(kept):,} scored "
           f"(dropped {len(df)-len(kept):,} penny/again NaN).  "
           "peak = max winning-leg prem over +40m / entry; exit = +40m / entry.\n")

    _table(kept, "(1) ALL near-expiry crossings (DTE ≤ 2.5):")
    _print("")
    _table(kept[kept["dte"] <= 1.5], "(2) 0-DTE crossings (DTE ≤ 1.5) — where the blast actually lives:")
    _print("")
    aft = kept[(kept["dte"] <= 1.5) & (kept["tod"] >= 13 * 60 + 45)]     # the real blast window
    _table(aft, "(2b) 0-DTE AFTERNOON crossings (≥13:45 IST) — the actual retail blast window "
                "(cheap ATM leg):")

    # (3) the headline the user asked for: TRUE vs WEAK ≥3× counts, split by expiry-structure era
    kept["era"] = kept["day"].map(expiry_era)
    _print("\n(3) 0-DTE AFTERNOON '≥3× peak jump' — TRUE vs WEAK by expiry-structure era (counts):")
    _print(f"    {'era':<42} {'TRUE n / ≥3×':>16} {'WEAK n / ≥3×':>16}")
    e0 = kept[(kept["dte"] <= 1.5) & (kept["tod"] >= 13 * 60 + 45)]
    for e in era_order(e0["era"].unique()):
        sub = e0[e0["era"] == e]
        true = sub[sub["grade"] == _BUCKETS[0][0]]
        weak = sub[sub["grade"] == _BUCKETS[1][0]]
        t3 = int((true["blast_mult_peak"] >= 3.0).sum())
        w3 = int((weak["blast_mult_peak"] >= 3.0).sum())
        _print(f"    {ERA_LABELS[e]:<42} {f'{len(true)} / {t3}':>16} {f'{len(weak)} / {w3}':>16}")

    _print("\n  Read: a useful grade ⇒ the TRUE bucket hits ≥3× (and ≥2×) MORE than WEAK/FAKE. If the "
           "≥3× rates are similar across buckets, the grade doesn't separate the lottery win — it only "
           "grades staying-power of the underlying move, not the premium payoff. ⚠️ Peak is a MID spike; "
           "real expiry-afternoon fills are worse (spreads widen exactly when premiums explode).")


if __name__ == "__main__":
    main()
