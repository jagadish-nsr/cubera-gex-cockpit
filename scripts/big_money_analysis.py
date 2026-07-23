"""Big-money analysis for a day ([W2]) — the size distribution + a value(price)/time/volume profile of
the captured big-money EVENTS (prints + bursts) from the SQLite store.

  python scripts/big_money_analysis.py                 # today, all symbols  (text report)
  python scripts/big_money_analysis.py 2026-07-23 NIFTY
  python scripts/big_money_analysis.py --json out.json  # also dump the aggregates as JSON (for a chart)

Honest scope: these are the ABOVE-FLOOR big-money events (prints >= ₹print-floor, bursts >= ₹burst-floor),
so the value distribution is the RIGHT TAIL of all orders, not the full population (that needs the raw
tick archive). Sign-free; the side is aggressor (Lee-Ready). Decision-support, not a signal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.big_money_db import load_events   # noqa: E402

DEFAULT_SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")
PRICE_BIN = {"NIFTY": 25.0, "BANKNIFTY": 100.0, "SENSEX": 100.0}
BUCKET_MIN = 15                            # time profile bucket (minutes)
_OPEN = 9 * 60 + 15


def _print(s: str = "") -> None:
    print(s, flush=True)


def _mins(hhmmss: str) -> int:
    h, m = hhmmss[11:16].split(":")
    return int(h) * 60 + int(m)


def _bar(x, xmax, width=28, ch="█"):
    n = int(round(width * (x / xmax))) if xmax else 0
    return ch * n


def analyse(day: str, symbol: str) -> dict | None:
    import numpy as np
    import pandas as pd
    ev = pd.DataFrame(load_events(symbol, day))
    if ev.empty:
        return None
    ev["cr"] = ev["notional"] / 1e7
    pr = ev[ev["kind"] == "print"].copy()
    bu = ev[ev["kind"] == "burst"].copy()
    out: dict = {"symbol": symbol, "day": day, "n_events": len(ev), "n_print": len(pr),
                 "n_burst": len(bu), "span": [ev["ts_ist"].min()[11:], ev["ts_ist"].max()[11:]]}

    # (1) print value distribution — ₹cr histogram + percentiles + lots
    if len(pr):
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

    # (2) time profile — per 15-min bucket: prints, bursts, net signed ₹ (prints only, side known)
    tb: dict[int, dict] = {}
    for _, r in ev.iterrows():
        b = ((_mins(r["ts_ist"]) - _OPEN) // BUCKET_MIN) * BUCKET_MIN + _OPEN
        d = tb.setdefault(b, {"print": 0, "burst": 0, "buy": 0.0, "sell": 0.0})
        d[r["kind"]] += 1
        if r["kind"] == "print" and r["side"] in ("buy", "sell"):
            d[r["side"]] += r["cr"]
    out["time"] = [{"t": f"{b // 60:02d}:{b % 60:02d}", "print": v["print"], "burst": v["burst"],
                    "buy": round(v["buy"], 2), "sell": round(v["sell"], 2),
                    "net": round(v["buy"] - v["sell"], 2)} for b, v in sorted(tb.items())]

    # (3) value profile — big-money PRINT notional by price level, split buy/sell (volume-at-price)
    if len(pr):
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


def report(a: dict) -> None:
    _print(f"\n═══ {a['symbol']}  ·  {a['n_events']} events "
           f"({a['n_print']} prints / {a['n_burst']} bursts)  ·  {a['span'][0]}–{a['span'][1]} ═══")
    if "dist" not in a:
        _print("  no prints."); return
    p = a["pctl"]
    _print(f"(1) PRINT VALUE distribution (≥ floor)  ·  Σ ₹{a['sum_cr']}cr  ·  "
           f"median {a['lots']['p50']:.0f} lots (max {a['lots']['max']:.0f})")
    cmax = max(a["dist"]["counts"]) or 1
    for e, c in zip(a["dist"]["edges"], a["dist"]["counts"]):
        _print(f"    ₹{e:>4.1f}-{e+0.5:>3.1f}cr {c:>4}  {_bar(c, cmax)}")
    _print(f"    pctl: p50 ₹{p['p50']}cr · p90 ₹{p['p90']}cr · p95 ₹{p['p95']}cr · p99 ₹{p['p99']}cr · "
           f"max ₹{p['max']}cr   sides buy {a['sides']['buy']} / sell {a['sides']['sell']} / mid {a['sides']['mid']}")

    _print(f"\n(2) TIME profile ({BUCKET_MIN}-min) — net big-money ₹cr (prints), + activity")
    nmax = max((abs(t["net"]) for t in a["time"]), default=1) or 1
    for t in a["time"]:
        s = "+" if t["net"] >= 0 else "-"
        _print(f"    {t['t']}  net {t['net']:>+6.1f}cr  {_bar(abs(t['net']), nmax, 20, '▓' if t['net']>=0 else '░')}"
               f"   ({t['print']}p/{t['burst']}b)")

    _print(f"\n(3) VALUE profile — big-money notional by price (buy vs sell)")
    vmax = max((v["total"] for v in a["value_profile"]), default=1) or 1
    for v in a["value_profile"]:
        _print(f"    {v['price']:>9,.0f}  ₹{v['total']:>5.1f}cr  "
               f"{_bar(v['buy'], vmax, 18, '▇')}{_bar(v['sell'], vmax, 18, '▁')}  "
               f"(B{v['buy']:.0f}/S{v['sell']:.0f})")


def main() -> None:
    from datetime import datetime, timedelta, timezone
    ap = argparse.ArgumentParser()
    ap.add_argument("args", nargs="*")
    ap.add_argument("--json", default=None)
    ns = ap.parse_args()
    day = next((a for a in ns.args if a.count("-") == 2),
               (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d"))
    syms = [a.upper() for a in ns.args if a.count("-") != 2] or list(DEFAULT_SYMBOLS)
    _print(f"Big-money analysis · {day}  (above-floor events; the right tail, not all orders)")
    results = [a for a in (analyse(day, s) for s in syms) if a]
    for a in results:
        report(a)
    if ns.json:
        Path(ns.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        _print(f"\n  wrote {ns.json}")
    if not results:
        _print("  no events for that day.")


if __name__ == "__main__":
    main()
