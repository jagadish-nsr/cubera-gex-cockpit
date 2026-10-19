"""Gamma-blast anchor-strike tick capture — headless driver (P0 of the blast-realtime effort).

On an EXPIRY day it waits to the 13:45 freeze, reads spot, resolves the ATM CALL+PUT (the anchor the
blast fixes), and captures their WS ticks to data/journal/blast_ticks/<SYM>/<day>.csv until 15:30.

  python scripts/run_blast_ticks.py --market-hours            # wait for 13:45 IST, stop 15:30 (scheduled task)
  python scripts/run_blast_ticks.py --any-day --seconds 120   # off-expiry smoke test (skip the expiry gate)
  python scripts/run_blast_ticks.py --symbols NIFTY --freeze 13:45

Only runs on a symbol whose NEAREST option expiry is today (the 0-DTE gate) unless --any-day. Reconnects
on drop; token 24h-capped → repaste data/.dhan_token. Exchange tick time. Sign-free; NEVER auto-executes."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.big_money import IST                              # noqa: E402
from app.blast_ticks import BlastTickCollector, set_contracts   # noqa: E402
from data.dhan_feed import resolve_blast_set               # noqa: E402
from data.dhan_live import fetch_index_ohlc                # noqa: E402
import config                                             # noqa: E402

CLOSE_MIN = config.FNO_CLOSE_MIN                          # 15:40 from 2026-08-03 (F&O extended)
_LOGF = None


def _print(s: str = "") -> None:
    print(s, flush=True)
    if _LOGF is not None:
        try:
            _LOGF.write(s + "\n"); _LOGF.flush()
        except Exception:  # noqa: BLE001
            pass


def _spot_from_chain(symbol: str):
    """Fallback spot = the option chain's underlyingValue (Dhan-primary, NSE-backup) — the SAME source the
    dashboard levels use, and it works when the index /marketfeed/quote is flaky. Only needs the anchor
    strike, so a slightly stale spot is fine. _fetch_live_chain returns (payload, source, ohlc, note)."""
    try:
        from app.data_access import _fetch_live_chain
        payload, *_ = _fetch_live_chain(config.get_index(symbol), None)
        sp = ((payload or {}).get("records") or {}).get("underlyingValue")
        return float(sp) if sp else None
    except Exception:  # noqa: BLE001
        return None


def _spot(symbol: str, tries: int = 5):
    """Live spot for the ATM anchor. Retries the cheap index quote (a single 13:45 429 shouldn't kill the
    whole day's capture), then falls back to the option chain's underlyingValue. None only if BOTH fail."""
    for i in range(tries):
        try:
            d = fetch_index_ohlc(symbol)
            if d and d.get("ltp") is not None:
                return float(d["ltp"])
        except Exception as e:  # noqa: BLE001
            _print(f"  spot quote try {i + 1}/{tries} failed: {type(e).__name__}")
        time.sleep(3)
    sp = _spot_from_chain(symbol)                        # fallback: the option chain's underlying
    if sp:
        _print(f"  spot from option chain (index quote unavailable): {sp:.1f}")
        return sp
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY")
    ap.add_argument("--freeze", default="13:45", help="HH:MM IST to fix the ATM anchor + start capture")
    ap.add_argument("--market-hours", action="store_true", help="wait for freeze, stop at 15:30 IST")
    ap.add_argument("--any-day", action="store_true", help="skip the expiry-day gate (for smoke tests)")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = until 15:30)")
    args = ap.parse_args()

    global _LOGF
    if args.market_hours:
        ld = ROOT / "data" / "journal" / "blast_ticks_logs"
        ld.mkdir(parents=True, exist_ok=True)
        _LOGF = open(ld / f"blast-{datetime.now(IST):%Y-%m-%d}.log", "a", encoding="utf-8")

    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    hh, mm = (int(x) for x in args.freeze.split(":"))
    now = datetime.now(IST)
    if now.weekday() >= 5:
        _print("Weekend — no session. Nothing to capture."); return
    try:
        from data.holidays import is_holiday
        if is_holiday(now.date()):
            _print("Holiday — exchange closed. Nothing to capture."); return
    except Exception:
        pass
    freeze_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    close_dt = now.replace(hour=CLOSE_MIN // 60, minute=CLOSE_MIN % 60, second=0, microsecond=0)

    if args.market_hours:
        if time.time() >= close_dt.timestamp():
            _print("Past 15:30 IST — session over. Nothing to capture."); return
        wait = freeze_dt.timestamp() - time.time()
        if wait > 0:
            _print(f"Waiting {wait/60:.0f} min for the {args.freeze} freeze (now {now:%H:%M} IST) …")
            time.sleep(wait)

    today = datetime.now(IST).strftime("%Y-%m-%d")
    stop_at = (time.time() + args.seconds) if args.seconds else (close_dt.timestamp() if args.market_hours else None)

    collectors = []
    for sym in symbols:
        sp = _spot(sym)
        if sp is None:
            _print(f"  {sym}: no spot — skipping."); continue
        bset = resolve_blast_set(sym, sp)
        if not bset:
            _print(f"  {sym}: could not resolve an ATM set — skipping."); continue
        if not args.any_day and bset["expiry"] != today:
            _print(f"  {sym}: not an expiry day (nearest expiry {bset['expiry']}) — skipping. "
                   f"Use --any-day to force."); continue
        col = BlastTickCollector(set_contracts(bset)).start()
        collectors.append((sym, col))
        wu = bset["wall_up"]["strike"] if bset.get("wall_up") else "—"
        wd = bset["wall_dn"]["strike"] if bset.get("wall_dn") else "—"
        _print(f"  {sym}: capturing anchor {bset['strike']:.0f} CE/PE + walls (up {wu} CE / dn {wd} PE) "
               f"exp {bset['expiry']} (spot {sp:.1f})")

    if not collectors:
        _print("Nothing to capture (no expiry symbol / no spot / no pair)."); return

    last_beat = 0.0
    try:
        while any(c.alive() for _, c in collectors) and (stop_at is None or time.time() < stop_at):
            time.sleep(2.0)
            if time.time() - last_beat >= 60:
                for sym, c in collectors:
                    s = c.status
                    _print(f"  · {datetime.now(IST):%H:%M} {sym} · {'live' if s.connected else (s.error or 'connecting')} "
                           f"· {s.n_ticks} ticks · CE/PE {s.per_side.get('CE', 0)}/{s.per_side.get('PE', 0)}")
                last_beat = time.time()
    except KeyboardInterrupt:
        _print("\n  stopped by user.")
    finally:
        for _, c in collectors:
            c.stop()
        time.sleep(1.5)
    for sym, c in collectors:
        _print(f"  done — {sym}: {c.status.n_ticks} ticks "
               f"(CE {c.status.per_side.get('CE', 0)} / PE {c.status.per_side.get('PE', 0)})"
               f"{'  · last error: ' + c.status.error if c.status.error else ''}")


if __name__ == "__main__":
    main()
