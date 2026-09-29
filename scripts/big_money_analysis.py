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

from app.big_money_analysis import DEFAULT_SYMBOLS, BUCKET_MIN, analyse   # noqa: E402


def _print(s: str = "") -> None:
    print(s, flush=True)


def _bar(x, xmax, width=28, ch="█"):
    n = int(round(width * (x / xmax))) if xmax else 0
    return ch * n


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

    if not a.get("has_oi"):
        _print("\n(4) POSITIONING (ΔOI) — no OI on these events (captured before ΔOI was added).")
        return
    from app.oi_footprint import classify_buildup
    od = a["oi_day"]
    _print(f"\n(4) POSITIONING (ΔOI) — day OI {od['open']:>12,.0f} → {od['close']:,.0f}  "
           f"({od['change']:+,.0f} → {'BUILDING' if od['change'] > 0 else 'UNWINDING'})")
    prev_oi = prev_px = None
    for t in a["time"]:
        if t["oi"] is None:
            continue
        if prev_oi is not None:
            doi, dpx = t["oi"] - prev_oi, (t["px"] - prev_px if prev_px else 0.0)
            q = classify_buildup(doi, dpx) or "no clear build"
            _print(f"    {t['t']}  ΔOI {doi:>+10,.0f}   px {t['px']:>9,.0f} ({dpx:>+6.0f})   {q}")
        prev_oi, prev_px = t["oi"], t["px"]
    if a.get("positioning"):
        _print("    print build-up ₹cr:  " + "  ·  ".join(f"{k} {v}" for k, v in a["positioning"].items()))
    c = a.get("conviction")
    if c:
        _print(f"    burst conviction (ΔOI/Δvol): net {c['net']:+.2f} "
               f"({'BUILDING' if c['net'] > 0 else 'UNWINDING' if c['net'] < 0 else 'churn'}) · "
               f"|conv| med {c['median_abs']:.2f} · positioning share {c['posn_share']:.0f}% of burst ₹ "
               f"(rest is churn)")


def main() -> None:
    from datetime import datetime, timedelta, timezone
    ap = argparse.ArgumentParser()
    ap.add_argument("args", nargs="*")
    ap.add_argument("--json", default=None)
    ap.add_argument("--db", default=None, help="point at a specific events DB (e.g. a re-derived one)")
    ns = ap.parse_args()
    if ns.db:
        import app.big_money_db as _db
        _db.DB_PATH = Path(ns.db)
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
