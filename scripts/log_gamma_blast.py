"""Gamma-blast 1-min capture — EXPIRY DAY only, 13:00-15:30 IST.

Captures per-strike OI/ΔOI + nearest-expiry premiums + gamma exposure every
minute through the blast window, into data/journal/gamma_blast/<SYMBOL>/YYYY-MM-DD.csv
(same schema as the daily oi_strikes capture, finer cadence). One pull per index.
Nifty expires Tuesday, Bank Nifty on the last Tuesday, Sensex on Thursday. A
holiday moves that contract to the previous trading day. The gate is the nearest
listed expiry on that index's own chain, not the weekday.

  python scripts/log_gamma_blast.py            # loop 1-min, all three indices
  python scripts/log_gamma_blast.py --once      # one tick if in-window + expiry
  python scripts/log_gamma_blast.py --symbol SENSEX
  python scripts/log_gamma_blast.py --once --force   # ignore the expiry/window gate

Reuses the dashboard pipeline (one fetch per index). A symbol whose nearest
expiry is not today is skipped. Decision-support only — no trades.
"""

from __future__ import annotations

import argparse
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data_access import load_dashboard  # noqa: E402
from app.timeseries import (GAMMA_BLAST_DIR, append_strike_rows,  # noqa: E402
                            build_strike_rows)

_WIN_START_IST = (13, 0)     # gamma-blast window 13:00-15:30 IST (afternoon)
_WIN_END_IST = (15, 30)
# Own chain each. Bank Nifty is monthly; Sensex is Thursday. Do not substring-match NIFTY.
BLAST_SYMBOLS = ("NIFTY", "BANKNIFTY", "SENSEX")


_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now() -> datetime:
    """Aware IST. A UTC clock shifted by 5:30 still carries a UTC tzinfo, and
    any later ``astimezone(IST)`` adds the offset a second time."""
    return datetime.now(_IST)


def _in_window(ist: datetime) -> bool:
    try:
        from data.holidays import is_trading_day
        if not is_trading_day(ist.date()):
            return False
    except Exception:
        if ist.weekday() >= 5:
            return False
    m = ist.hour * 60 + ist.minute
    return _WIN_START_IST[0] * 60 + _WIN_START_IST[1] <= m <= _WIN_END_IST[0] * 60 + _WIN_END_IST[1]


def resolve_symbols(explicit: str | None) -> tuple[str, ...]:
    """Default is all three indices. A comma list restricts the pull."""
    if not explicit:
        return BLAST_SYMBOLS
    out = tuple(part.strip().upper() for part in explicit.split(",") if part.strip())
    return out or BLAST_SYMBOLS


def capture_once(force: bool = False, symbol: str | None = None) -> str:
    data = load_dashboard(symbol=symbol)  # live NSE, single fetch
    ist = data.fetched_at + timedelta(hours=5, minutes=30)
    ist_today = ist.date()
    nearest = data.expiries[0] if data.expiries else None
    is_expiry = (nearest == ist_today)
    if not force and not is_expiry:
        return (f"[{data.symbol}] {ist:%Y-%m-%d %H:%M} IST  not an expiry day "
                f"(nearest {nearest}) — skipped")

    rows = build_strike_rows(data)
    if not rows:
        return f"[{data.symbol}] {ist:%Y-%m-%d %H:%M} IST  no per-strike data (live feed only) — skipped"
    from app.timeseries import gamma_blast_dir_for
    path = append_strike_rows(rows, date_ist=ist.strftime("%Y-%m-%d"),
                              symbol=data.symbol, directory=gamma_blast_dir_for(data.source))
    return (f"[{data.symbol}] {ist:%Y-%m-%d} {ist:%H:%M} IST  spot {data.levels.spot:,.1f}  "
            f"magnet {data.levels.magnet:,.0f}  {len(rows)} strikes -> {path.name}")


def _note(msg: str | None = None, error=None) -> None:
    from app.obs import event
    if error is not None:
        event("gamma_blast.failed", level="WARNING", ok=False, workflow="gamma_blast", error=str(error))
        return
    text = msg or ""
    event(
        "gamma_blast.skipped" if "skipped" in text else "gamma_blast.appended",
        workflow="gamma_blast",
        message=text[:400],
    )


def _print(s: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def capture_symbols(force: bool = False, explicit: str | None = None) -> list[str]:
    """One pull per index. A symbol that is not expiring today is skipped in the message."""
    messages = []
    for sym in resolve_symbols(explicit):
        try:
            msg = capture_once(force=force, symbol=sym)
        except Exception as e:  # noqa: BLE001 - one index must not drop the others
            msg = f"[{sym}] capture FAILED: {e}"
            _note(error=e)
        messages.append(msg)
        _print(msg)
        if "FAILED" not in msg:
            _note(msg)
    return messages


def main() -> None:
    ap = argparse.ArgumentParser(description="Expiry-day 1-min gamma-blast capture.")
    ap.add_argument("--once", action="store_true", help="single tick then exit (schedulers)")
    ap.add_argument("--symbol", default=None,
                    help="NIFTY, BANKNIFTY, and/or SENSEX (comma list). Default: all three")
    ap.add_argument("--loop", type=float, default=1.0, help="interval in minutes (loop mode)")
    ap.add_argument("--force", action="store_true",
                    help="ignore the expiry-day / window gate (testing)")
    args = ap.parse_args()
    from app.obs import boot, shutdown
    boot("log_gamma_blast", loop=True)

    if args.once:
        ist = _ist_now()
        if not args.force and not _in_window(ist):
            _print(f"{ist:%Y-%m-%d %H:%M} IST  outside 13:00-15:30 window - skipped")
            shutdown(ok=True, message="outside window")
            return
        messages = capture_symbols(force=args.force, explicit=args.symbol)
        failed = [m for m in messages if "FAILED" in m]
        if failed and len(failed) == len(messages):
            shutdown(ok=False, error=failed[0])
            sys.exit(1)
        shutdown(ok=True, message="captured")
        return

    interval = max(args.loop, 0.25) * 60.0
    names = ", ".join(resolve_symbols(args.symbol))
    _print(f"Gamma-blast capture: every {args.loop:g} min, 13:00-15:30 IST, "
           f"expiry day, {names}. Ctrl+C to stop.")
    try:
        while True:
            ist = _ist_now()
            if args.force or _in_window(ist):
                capture_symbols(force=args.force, explicit=args.symbol)
            else:
                _print(f"{ist:%Y-%m-%d %H:%M} IST  outside window - idle")
            _time.sleep(interval)
    except KeyboardInterrupt:
        _print("stopped.")
        shutdown(ok=True, message="stopped")


if __name__ == "__main__":
    main()
