"""Train + honestly evaluate a TRUE-gamma-blast predictor on the leak-free crossing dataset.

Target: BIG blast = continuation > BIG_PCT over [entry, +30min] (a tradeable sustained move).
Question: from point-in-time features at entry, can we flag the ~18% that become big blasts?

Discipline (this is where fake edges die):
  • WALK-FORWARD split — train on earlier days, test on later (never random CV; events overlap
    and cluster by day, so random folds leak).
  • Baselines to beat: base rate, and single-feature momentum / blast-ignition (cover) / turnover.
  • SHUFFLE control — retrain on a permuted target; test AUC must collapse to ~0.5.
  • EV eval — for events the model flags (top predicted-prob decile), the mean continuation and
    P&L-with-a-stop, vs the unconditional base. AUC 0.55 is meaningless if EV ≤ costs.

  python scripts/train_blast_model.py            # NIFTY  [big_pct]

⚠️ Prior is low: our point-in-time blast test (Study #5) was ~0. This is the rigorous check."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FEATURES = ["cover", "thrust", "wall_ratio", "wall_erode", "mom_obs", "iv", "div_obs",
            "skew", "turnover", "pcr", "rv_sofar", "absg", "dist_step", "dir", "dte", "tod"]


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def main() -> None:
    import numpy as np
    import pandas as pd
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler

    store = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    big = float(sys.argv[2]) if len(sys.argv) > 2 else 0.15
    df = pd.read_parquet(ROOT / "data" / "journal" / "blast_ml" / f"{store}.parquet")
    df = df.sort_values("day").reset_index(drop=True)
    df["y"] = (df["cont"] > big).astype(int)

    # walk-forward: first 70% of DAYS train, last 30% test
    days = sorted(df["day"].unique())
    cut = days[int(len(days) * 0.70)]
    tr, te = df[df["day"] < cut], df[df["day"] >= cut]
    Xtr, Xte = tr[FEATURES].to_numpy(float), te[FEATURES].to_numpy(float)
    ytr, yte = tr["y"].to_numpy(), te["y"].to_numpy()
    _print(f"TRUE-gamma-blast model · {store} · target cont>{big}%\n")
    _print(f"  train {len(tr):,} crossings (< {cut}) · test {len(te):,} (≥ {cut})")
    _print(f"  base rate big-blast: train {ytr.mean()*100:.1f}%  test {yte.mean()*100:.1f}%\n")

    gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300,
                                        l2_regularization=1.0, min_samples_leaf=60,
                                        random_state=0).fit(Xtr, ytr)
    p_gb = gb.predict_proba(Xte)[:, 1]
    auc_gb = roc_auc_score(yte, p_gb)
    auc_tr = roc_auc_score(ytr, gb.predict_proba(Xtr)[:, 1])

    # logistic (median-impute + scale)
    med = np.nanmedian(Xtr, axis=0)
    Xtr_i = np.where(np.isnan(Xtr), med, Xtr)
    Xte_i = np.where(np.isnan(Xte), med, Xte)
    sc = StandardScaler().fit(Xtr_i)
    lr = LogisticRegression(max_iter=1000, C=0.5).fit(sc.transform(Xtr_i), ytr)
    auc_lr = roc_auc_score(yte, lr.predict_proba(sc.transform(Xte_i))[:, 1])

    _print("Test-set AUC (0.50 = no skill):")
    _print(f"  gradient boosting : {auc_gb:.3f}   (train {auc_tr:.3f} — gap = overfit)")
    _print(f"  logistic          : {auc_lr:.3f}")
    for f in ("mom_obs", "cover", "turnover", "absg"):
        s = te[[f]].to_numpy(float).ravel()
        m = ~np.isnan(s)
        a = roc_auc_score(yte[m], s[m]) if m.sum() > 50 else float("nan")
        _print(f"  single: {f:9s}  : {max(a, 1-a):.3f}")

    # shuffle control
    rng = np.random.default_rng(0)
    gb_s = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300,
                                          l2_regularization=1.0, min_samples_leaf=60,
                                          random_state=0).fit(Xtr, rng.permutation(ytr))
    _print(f"  SHUFFLE control   : {roc_auc_score(yte, gb_s.predict_proba(Xte)[:, 1]):.3f}  "
           "(should be ~0.50)\n")

    # EV eval: bucket test by predicted-prob decile
    te = te.copy()
    te["p"] = p_gb
    te["dec"] = pd.qcut(te["p"], 10, labels=False, duplicates="drop")
    _print("Test crossings by predicted-prob decile (EV of acting on the model's pick):")
    _print(f"  {'decile':>7} {'n':>5} {'%big':>6} {'mean cont%':>11} {'mean MFE':>9} {'mean MAE':>9}")
    for dcl in sorted(te["dec"].dropna().unique()):
        g = te[te["dec"] == dcl]
        _print(f"  {int(dcl):>7} {len(g):>5} {100*(g['cont']>big).mean():>5.0f}% "
               f"{g['cont'].mean():>+10.3f}% {g['mfe'].mean():>+8.2f}% {g['mae'].mean():>+8.2f}%")
    top = te[te["dec"] == te["dec"].max()]
    base = te
    _print(f"\n  top decile: mean cont {top['cont'].mean():+.3f}% vs base {base['cont'].mean():+.3f}% · "
           f"%big {100*(top['cont']>big).mean():.0f}% vs {100*(base['cont']>big).mean():.0f}%")
    imp = pd.Series(gb.feature_importances_ if hasattr(gb, "feature_importances_") else
                    np.zeros(len(FEATURES)), index=FEATURES)
    if imp.abs().sum() == 0:                       # HGB has no native importances → permutation
        from sklearn.inspection import permutation_importance
        pi = permutation_importance(gb, Xte, yte, n_repeats=5, random_state=0, scoring="roc_auc")
        imp = pd.Series(pi.importances_mean, index=FEATURES)
    _print("\n  Top features: " + ", ".join(f"{k} {v:.3f}" for k, v in imp.sort_values(ascending=False).head(6).items()))
    _print("\n  Verdict: skill exists only if test AUC > ~0.55 AND the top-decile EV clears costs "
           "AND the shuffle control is ~0.50. Otherwise it's the same ~0 point-in-time edge.")


if __name__ == "__main__":
    main()
