"""Big-money futures tape — headless collector ([W2], docs/big-money-tape.md).

A thin CLI over app.big_money_live.Collector (the same engine the dashboard auto-starts): streams the
current-expiry NIFTY / BankNifty / SENSEX futures, saves EVERY raw Quote/Full tick to
data/journal/big_money_raw/<SYM>/<day>.csv, and appends big prints/bursts to the JSONL event store.

  python scripts/run_big_money_collector.py                 # run until Ctrl-C
  python scripts/run_big_money_collector.py --market-hours   # wait for 09:15 IST, stop at 15:30 IST (for a scheduled task)
  python scripts/run_big_money_collector.py --dry-run        # print only — no raw, no events (calibration)
  python scripts/run_big_money_collector.py --seconds 120    # bounded test run
  python scripts/run_big_money_collector.py --print-floor 2000000 --burst-floor 5000000   # floor overrides

Reconnects on drop; token 24h-capped → repaste data/.dhan_token and it resumes on the next retry (so a
scheduled run that starts before you paste the morning token will connect once it's there). Exchange
tick time. Sign-free; decision-support; NEVER auto-executes. Token not logged."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:                                                   # emojis/₹ in help + logs → utf-8 (cp1252 console/redirect)
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.big_money import IST                          # noqa: E402
from app.big_money_live import Collector               # noqa: E402
import config                                           # noqa: E402

OPEN_MIN, CLOSE_MIN = config.MARKET_OPEN_MIN, config.FNO_CLOSE_MIN   # 09:15–15:40 (F&O, from 2026-08-03)


_LOGF = None            # optional file handle mirroring _print (for the scheduled task's log)


def _print(s: str = "") -> None:
    print(s, flush=True)
    if _LOGF is not None:
        try:
            _LOGF.write(s + "\n")
            _LOGF.flush()
        except Exception:  # noqa: BLE001
            pass


def _fmt(ev) -> str:
    arrow = {"buy": "▲BUY ", "sell": "▼SELL", None: "  •  "}[ev.side]
    lots = f"{ev.lots:.0f}L" if ev.lots is not None else "?"
    tag = "PRINT" if ev.kind == "print" else "BURST"
    return f"  {ev.ts_ist[11:]}  {ev.symbol:<9} {arrow} {tag}  {lots:>5}  ₹{ev.notional/1e7:>5.2f}cr  @{ev.price:.1f}"


def _market_window():
    """Today's (open_epoch, close_epoch) in true epochs, or None on a weekend or exchange holiday."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return None
    try:
        from data.holidays import is_holiday
        if is_holiday(now.date()):
            return None
    except Exception:
        pass
    o = now.replace(hour=9, minute=15, second=0, microsecond=0)
    c = now.replace(hour=CLOSE_MIN // 60, minute=CLOSE_MIN % 60, second=0, microsecond=0)
    return o.timestamp(), c.timestamp()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print only, no raw/events written")
    ap.add_argument("--market-hours", action="store_true",
                    help="wait for 09:15 IST, stop at 15:30 IST (for a scheduled daily task)")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = forever)")
    ap.add_argument("--symbols", default="NIFTY,BANKNIFTY,SENSEX")
    ap.add_argument("--print-floor", type=float, default=None, help="₹ single-print floor (all symbols)")
    ap.add_argument("--burst-floor", type=float, default=None, help="₹ window-burst floor (all symbols)")
    ap.add_argument("--log", action="store_true",
                    help="also append output to data/journal/big_money_logs/ (auto-on for --market-hours)")
    args = ap.parse_args()
    from app.obs import boot, shutdown
    boot("big_money", loop=True)

    global _LOGF
    if args.log or args.market_hours:
        ld = ROOT / "data" / "journal" / "big_money_logs"
        ld.mkdir(parents=True, exist_ok=True)
        _LOGF = open(ld / f"collector-{datetime.now(IST):%Y-%m-%d}.log", "a", encoding="utf-8")

    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    pf = {s: args.print_floor for s in symbols} if args.print_floor is not None else None
    bf = {s: args.burst_floor for s in symbols} if args.burst_floor is not None else None

    stop_at = None
    if args.market_hours:
        win = _market_window()
        if win is None:
            _print("Weekend — market closed. Nothing to collect.")
            shutdown(ok=True, message="weekend")
            return
        open_e, stop_at = win
        now = time.time()
        if now >= stop_at:
            _print("Past 15:30 IST — market closed for today. Nothing to collect.")
            shutdown(ok=True, message="session over")
            return
        if now < open_e:
            wait = open_e - now
            _print(f"Waiting {wait/60:.0f} min for 09:15 IST open "
                   f"(now {datetime.now(IST):%H:%M} IST) …")
            time.sleep(wait)
    elif args.seconds:
        stop_at = time.time() + args.seconds

    col = Collector(symbols=symbols, save_raw=not args.dry_run, persist_events=not args.dry_run,
                    print_floor=pf, burst_floor=bf, on_event=lambda ev: _print(_fmt(ev)))
    _print(f"  mode: {'DRY-RUN (no writes)' if args.dry_run else 'LIVE → raw + events'}"
           f"{'  · stop at 15:30 IST' if args.market_hours else ''}"
           f"{'  · floors print=%s burst=%s' % (args.print_floor, args.burst_floor) if (pf or bf) else ''}")
    col.start()

    last_beat = 0.0
    try:
        while col.alive() and (stop_at is None or time.time() < stop_at):    # alive(), not status.running (GIL race)
            time.sleep(2.0)
            if time.time() - last_beat >= 60:         # heartbeat so a scheduled log shows liveness
                s = col.status
                _print(f"  · {datetime.now(IST):%H:%M} IST · {'live' if s.connected else (s.error or 'connecting')}"
                       f" · {s.n_raw:,} raw · {s.n_events} events · ticks {s.per_symbol}")
                last_beat = time.time()
    except KeyboardInterrupt:
        _print("\n  stopped by user.")
    finally:
        col.stop()
        time.sleep(1.5)                               # let the final raw flush land
        shutdown(ok=True, message="stopped", ticks=col.status.n_raw, prints=col.status.n_events)
    s = col.status
    _print(f"\n  done — {s.n_raw:,} raw ticks, {s.n_events} big-money events. ticks {s.per_symbol}"
           f"{'  · last error: ' + s.error if s.error else ''}"
           f"{'  · never connected (network/token at open?)' if s.n_raw == 0 and s.n_events == 0 else ''}")

    if args.market_hours and not args.dry_run:       # session over → compact today's raw CSVs to Parquet
        from app.big_money_live import compact_raw_day
        today = datetime.now(IST).strftime("%Y-%m-%d")
        for sym in symbols:
            rows = compact_raw_day(sym, today)
            if rows:
                _print(f"  compacted {sym}/{today}: {rows:,} rows → Parquet")


if __name__ == "__main__":
    main()
