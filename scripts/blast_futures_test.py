"""Does adding INTRADAY FUTURES (OI build-up + momentum + turnover) help the blast model?

Only ~50 trading days have Dhan intraday futures (90-day cap), so this is a small, underpowered
test — run honestly with that caveat. We build the crossing set on those days (options features +
the 3 futures features), then compare options-only vs options+futures under walk-forward CV, plus
the univariate futures-vs-outcome correlations.

  python scripts/blast_futures_test.py            # NIFTY

⚠️ n is small (~one contract's life); treat AUC deltas as suggestive, not conclusive."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build_blast_dataset import _day_rows, fut_map  # noqa: E402  (sibling script)

OPT = ["cover", "thrust", "wall_ratio", "wall_erode", "mom_obs", "iv", "div_obs", "skew",
       "turnover", "pcr", "rv_sofar", "absg", "dist_step", "dir", "dte", "tod"]
FUT = ["fut_doi", "fut_mom", "fut_to"]


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _grid_stamps(store, day):
    from data.dhan import load_intraday_day
    from build_blast_dataset import _grid
    df = load_intraday_day(store, day)
    return _grid(df) if df is not None and not df.empty else []


def main() -> None:
    import numpy as np
    import pandas as pd
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    from app.intraday_features import load_features
    from data.dhan import DHAN_FUT_DIR

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    futdir = DHAN_FUT_DIR / store
    fut_days = sorted(p.stem for p in futdir.glob("*.parquet")) if futdir.exists() else []
    if not fut_days:
        _print(f"No intraday futures for {store}.")
        return
    feat = load_features(store)
    dte_by_day = feat.groupby("day")["dte"].first().to_dict()
    gex_map = {(r.day, r.time_ist): abs(r.net_gex) for r in feat.itertuples()}

    rows = []
    for d in fut_days:
        stamps = _grid_stamps(store, d)
        fm = fut_map(store, d, stamps) if stamps else None
        rows.extend(_day_rows(store, d, dte_by_day.get(d, np.nan), gex_map, futm=fm))
    df = pd.DataFrame(rows).sort_values("day").reset_index(drop=True)
    df["y"] = (df["cont"] > 0.15).astype(int)
    df = df.dropna(subset=FUT, how="all")
    _print(f"Futures-augmented blast test · {store} · {df['day'].nunique()} futures days · "
           f"{len(df):,} crossings · base big-blast {100*df['y'].mean():.1f}%\n")

    # univariate: do the futures features relate to the outcome at all?
    _print("Univariate Spearman (futures feature vs continuation %):")
    for f in FUT:
        s = df[[f, "cont"]].dropna()
        _print(f"  {f:8s}: {s[f].corr(s['cont'], method='spearman'):+.3f}  (n={len(s)})")

    # walk-forward CV (5 expanding folds by day)
    days = sorted(df["day"].unique())
    folds = np.array_split(days, 6)

    def cv_auc(cols):
        aucs = []
        for k in range(1, len(folds)):
            trd = set(np.concatenate(folds[:k]))
            ted = set(folds[k])
            tr, te = df[df["day"].isin(trd)], df[df["day"].isin(ted)]
            if te["y"].nunique() < 2 or len(tr) < 100:
                continue
            m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=250,
                                               l2_regularization=1.0, min_samples_leaf=40,
                                               random_state=0).fit(tr[cols].to_numpy(float), tr["y"])
            aucs.append(roc_auc_score(te["y"], m.predict_proba(te[cols].to_numpy(float))[:, 1]))
        return float(np.mean(aucs)) if aucs else float("nan"), len(aucs)

    a_opt, n1 = cv_auc(OPT)
    a_all, n2 = cv_auc(OPT + FUT)
    a_fut, n3 = cv_auc(FUT)
    _print("\nWalk-forward CV test AUC (0.50 = no skill):")
    _print(f"  options-only        : {a_opt:.3f}  ({n1} folds)")
    _print(f"  options + futures   : {a_all:.3f}")
    _print(f"  futures-only        : {a_fut:.3f}")
    _print(f"  Δ from adding futures: {a_all - a_opt:+.3f}")
    _print("\n  Read: futures ADD value only if options+futures > options-only by a clear margin AND "
           "futures-only > 0.55. A ~0 delta / futures-only ~0.50 = no incremental signal (expected, "
           "given the arc's futures-OI null). Small n — suggestive only.")


if __name__ == "__main__":
    main()
