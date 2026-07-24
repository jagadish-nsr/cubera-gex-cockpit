"""Big-money day analysis — the reusable compute (the CLI report + JSON live in
scripts/big_money_analysis.py; the dashboard readout + replay call ``analyse`` / ``analyse_frame``).

Given a day's EVENTS it returns per symbol: the print value distribution, a 15-min time profile, a
value(price) profile, and §4 positioning (day OI trend, per-print build-up, burst conviction). Works on
either the live DB (``analyse``) or an arbitrary event list (``analyse_frame`` — used by replay on
re-derived events). Sign-free; decision-support."""

from __future__ import annotations

DEFAULT_SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")
PRICE_BIN = {"NIFTY": 25.0, "BANKNIFTY": 100.0, "SENSEX": 100.0}
BUCKET_MIN = 15
_OPEN = 9 * 60 + 15


def _mins(hhmmss: str) -> int:
    h, m = hhmmss[11:16].split(":")
    return int(h) * 60 + int(m)


def time_buckets(ev, bucket_min: int) -> list[dict]:
    """Per-bucket rollup at ``bucket_min`` granularity: prints/bursts, net signed ₹, and the OI level +
    price at the END of the bucket (for the build-up × time heatmap). ``ev`` must have a ``cr`` column."""
    tb: dict[int, dict] = {}
    for _, r in ev.iterrows():
        b = ((_mins(r["ts_ist"]) - _OPEN) // bucket_min) * bucket_min + _OPEN
        d = tb.setdefault(b, {"print": 0, "burst": 0, "buy": 0.0, "sell": 0.0, "oi": None, "px": None})
        d[r["kind"]] += 1
        if r["kind"] == "print" and r["side"] in ("buy", "sell"):
            d[r["side"]] += r["cr"]
        if r["oi"] == r["oi"]:
            d["oi"] = float(r["oi"])
        d["px"] = float(r["price"])
    return [{"t": f"{b // 60:02d}:{b % 60:02d}", "print": v["print"], "burst": v["burst"],
             "buy": round(v["buy"], 2), "sell": round(v["sell"], 2), "net": round(v["buy"] - v["sell"], 2),
             "oi": v["oi"], "px": v["px"]} for b, v in sorted(tb.items())]


def analyse(day: str, symbol: str) -> dict | None:
    """Analyse a symbol/day from the live event DB."""
    import pandas as pd
    from app.big_money_db import load_events
    return analyse_frame(pd.DataFrame(load_events(symbol, day)), symbol, day)


def analyse_frame(ev, symbol: str, day: str) -> dict | None:
    """Analyse an events DataFrame (dicts with the event columns). None if empty."""
    import numpy as np
    import pandas as pd
    from app.big_money import buildup
    if ev is None or getattr(ev, "empty", True):
        return None
    ev = ev.sort_values("ts_ist").reset_index(drop=True)
    ev["cr"] = pd.to_numeric(ev["notional"], errors="coerce") / 1e7
    ev["price"] = pd.to_numeric(ev["price"], errors="coerce")
    for col in ("oi", "doi", "conv"):
        if col not in ev.columns:
            ev[col] = None
        ev[col] = pd.to_numeric(ev[col], errors="coerce")
    pr = ev[ev["kind"] == "print"].copy()
    bu = ev[ev["kind"] == "burst"].copy()
    out: dict = {"symbol": symbol, "day": day, "n_events": len(ev), "n_print": len(pr),
                 "n_burst": len(bu), "span": [ev["ts_ist"].min()[11:], ev["ts_ist"].max()[11:]]}

    if len(pr):                                                     # (1) print value distribution
        cr = pr["cr"]
        edges = list(np.arange(1.0, float(cr.max()) + 0.5, 0.5))
        hist, _ = np.histogram(cr, bins=edges + [edges[-1] + 0.5])
        out["dist"] = {"edges": [round(e, 2) for e in edges], "counts": hist.tolist()}
        out["pctl"] = {k: round(float(cr.quantile(v)), 2)
                       for k, v in {"p50": .5, "p75": .75, "p90": .9, "p95": .95, "p99": .99}.items()}
        out["pctl"]["max"] = round(float(cr.max()), 2)
        out["sum_cr"] = round(float(cr.sum()), 1)
        out["lots"] = {"p50": float(pr["lots"].median()), "max": float(pr["lots"].max())}
        out["sides"] = {s: int((pr["side"] == s).sum()) for s in ("buy", "sell")}
        out["sides"]["mid"] = int(pr["side"].isna().sum())

    out["time"] = time_buckets(ev, BUCKET_MIN)                      # (2) time profile — 15-min
    out["time3"] = time_buckets(ev, 3)                             # + 3-min for the finer heatmap

    oi_ser = ev["oi"].dropna()                                     # (4) positioning
    out["has_oi"] = bool(len(oi_ser))
    if len(oi_ser):
        out["oi_day"] = {"open": float(oi_ser.iloc[0]), "close": float(oi_ser.iloc[-1]),
                         "change": float(oi_ser.iloc[-1] - oi_ser.iloc[0])}
        pos: dict[str, float] = {}
        for _, r in pr.iterrows():
            direction = 1 if r["side"] == "buy" else -1 if r["side"] == "sell" else 0
            q = buildup(direction, r["doi"] if r["doi"] == r["doi"] else None)
            if q:
                pos[q] = pos.get(q, 0.0) + r["cr"]
        out["positioning"] = {k: round(v, 1) for k, v in sorted(pos.items(), key=lambda x: -x[1])}
        bc = bu[bu["conv"].notna()].copy()
        if len(bc) and bc["cr"].sum():
            burst_cr = bc["cr"].sum()
            out["conviction"] = {
                "net": round(float((bc["conv"] * bc["cr"]).sum() / burst_cr), 3),
                "median_abs": round(float(bc["conv"].abs().median()), 3),
                "posn_share": round(float(bc.loc[bc["conv"].abs() >= 0.25, "cr"].sum() / burst_cr * 100), 0)}

    if len(pr):                                                    # (3) value profile
        step = PRICE_BIN.get(symbol, 25.0)
        pr["pbin"] = (pr["price"] // step * step).astype(float)
        vp = []
        for pb, g in pr.groupby("pbin"):
            buy = g.loc[g["side"] == "buy", "cr"].sum()
            sell = g.loc[g["side"] == "sell", "cr"].sum()
            mid = g.loc[g["side"].isna(), "cr"].sum()
            vp.append({"price": float(pb), "buy": round(buy, 2), "sell": round(sell, 2),
                       "mid": round(mid, 2), "total": round(buy + sell + mid, 2)})
        out["value_profile"] = sorted(vp, key=lambda r: r["price"], reverse=True)
    return out
