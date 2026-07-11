"""Does FUTURES build-up direction predict returns DIFFERENTLY by gamma-vol regime?

The hypothesis behind conditioning: |GEX| tells us the vol regime — LOW |GEX| = wilder/
TRENDING, HIGH |GEX| = calmer/MEAN-REVERTING (validated, docs/findings-summary.md). So the
futures build-up DIRECTION (price×ΔOI: long build-up / short build-up etc.), which showed no
edge POOLED, might work in the trending (low-gamma) regime and fail (revert) in the pinning
(high-gamma) regime — a regime-conditional edge the pooled test would wash out.

Per day (543 days of raw bhavcopy + cached |GEX|): near-month futures ΔPrice, aggregate
futures ΔOI & volume → build-up bias; bucket by |GEX| tercile; within each, Spearman IC of
(futures direction) vs forward 1/2/5-day return + mean return by build-up state.

  python scripts/analyze_gex_regime_futures.py            # NIFTY

⚠️ Exploratory; sign-free; overlapping horizons autocorrelate; one regime. Not a trade rule."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bhavcopy_raw import load_raw_fo_day  # noqa: E402
from app.gex_history import gex_series  # noqa: E402
from app.futures_buildup import buildup_bias, classify_futures_buildup  # noqa: E402

HORIZONS = [1, 2, 5]


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _bias_sign(label):
    b = buildup_bias(label)
    return 1 if b and b.startswith("bull") else -1 if b and b.startswith("bear") else 0


def _day_row(day: str, symbol: str) -> dict | None:
    df = load_raw_fo_day(day, symbol)
    if df is None:
        return None
    fut = df[df["instr"] == "IDF"].copy()
    if fut.empty or fut["expiry"].isna().all():
        return None
    spot = float(pd.to_numeric(df["spot"], errors="coerce").dropna().iloc[0])
    fut = fut.sort_values("expiry")
    near = fut.iloc[0]
    dprice = float(near["close"] - near["prev_close"]) if pd.notna(near["close"]) and pd.notna(near["prev_close"]) else None
    agg_doi = float(fut["doi"].sum())
    agg_vol = float(fut["volume"].sum())
    if dprice is None:
        return None
    state = classify_futures_buildup(dprice, agg_doi)
    return {"day": day, "spot": spot, "dprice": dprice, "agg_doi": agg_doi,
            "agg_vol": agg_vol, "state": state, "bias": _bias_sign(state),
            "mom": float(np.sign(dprice))}


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    gex = gex_series(symbol, refresh=False)
    rows = []
    for d in sorted(gex):
        r = _day_row(d, symbol)
        if r:
            r["absgex"] = abs(gex[d])
            rows.append(r)
    if len(rows) < 30:
        _print(f"Only {len(rows)} days — need the gex_history cache + raw bhavcopy.")
        return
    df = pd.DataFrame(rows).reset_index(drop=True)
    spot = df["spot"].to_numpy()
    for h in HORIZONS:
        fwd = np.full(len(df), np.nan)
        for i in range(len(df) - h):
            fwd[i] = (spot[i + h] / spot[i] - 1.0) * 100.0
        df[f"fwd_{h}"] = fwd

    q1, q2 = df["absgex"].quantile([1 / 3, 2 / 3])
    df["regime"] = np.where(df["absgex"] <= q1, "LOW |GEX| (trending)",
                            np.where(df["absgex"] >= q2, "HIGH |GEX| (pinning)", "MID"))

    _print(f"Futures build-up direction → forward return, BY gamma-vol regime  ·  {symbol}  "
           f"·  {len(df)} days\n")
    _print("Futures data BY regime (how the futures tape looks in each gamma regime):")
    for reg in ("LOW |GEX| (trending)", "MID", "HIGH |GEX| (pinning)"):
        sub = df[df["regime"] == reg]
        vol = sub["agg_vol"].mean()
        adoi = sub["agg_doi"].abs().mean()
        mix = sub["state"].value_counts(normalize=True)
        top = ", ".join(f"{k.split()[0]}-{k.split()[1][:3]} {v*100:.0f}%" for k, v in mix.head(3).items())
        _print(f"  {reg:22s}: mean vol {vol:,.0f}  mean |ΔOI| {adoi:,.0f}  · states: {top}")
    _print()
    _print("Spearman IC of futures build-up BIAS vs forward return (and momentum sign(ΔPrice)):")
    for reg in ("LOW |GEX| (trending)", "MID", "HIGH |GEX| (pinning)"):
        sub = df[df["regime"] == reg]
        cells = []
        for h in HORIZONS:
            b = sub[["bias", f"fwd_{h}"]].dropna()
            m = sub[["mom", f"fwd_{h}"]].dropna()
            ib = b["bias"].corr(b[f"fwd_{h}"], method="spearman") if len(b) >= 10 else float("nan")
            im = m["mom"].corr(m[f"fwd_{h}"], method="spearman") if len(m) >= 10 else float("nan")
            cells.append(f"{h}d bias {ib:+.2f}/mom {im:+.2f}")
        _print(f"  {reg:22s} (n={len(sub)}):  " + "   ".join(cells))

    _print("\nMean next-day return by build-up state × regime  (fresh-money states only):")
    for st in ("long build-up", "short build-up"):
        line = []
        for reg in ("LOW |GEX| (trending)", "HIGH |GEX| (pinning)"):
            s = df[(df["state"] == st) & (df["regime"] == reg)]["fwd_1"].dropna()
            line.append(f"{reg.split()[0]} {s.mean():+.2f}%/{(s>0).mean()*100:.0f}% (n={len(s)})" if len(s) else f"{reg.split()[0]} —")
        _print(f"  {st:16s}: " + "   ".join(line))

    _print("\n  Hypothesis: futures direction should work BETTER (more POSITIVE IC / continuation) "
           "in LOW-|GEX| trending days than in HIGH-|GEX| pinning days.")
    _print("⚠️ EXPLORATORY — sign-free; overlapping horizons autocorrelate; one regime. Not a trade rule.")


if __name__ == "__main__":
    main()
