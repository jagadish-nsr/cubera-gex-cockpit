"""Refresh both inputs of the margin-stress map after the close.

  1. NSE Clearing's end-of-day SPAN file -> data/journal/span/
  2. Dhan near-month futures tape (NIFTY, BANKNIFTY) -> data/journal/dhan_futures/

Then checks that each tape's latest session is the SPAN date, which is the condition
the map needs before it draws bands. Exits 0 when everything is ready or the day is
an exchange holiday, and 1 on any failure (so cron logs show it and a retry can run).

  python3 scripts/refresh_liquidation_inputs.py              # today, IST
  python3 scripts/refresh_liquidation_inputs.py 2026-11-23   # a specific day

SENSEX is not refreshed: the Dhan futures backfill covers NIFTY and BANKNIFTY only.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from analytics.span_fetch import SpanNotPublished, fetch_span  # noqa: E402

TAPE_SYMBOLS = ("NIFTY", "BANKNIFTY")


def _say(msg: str = "") -> None:
    stamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
    print(f"[{stamp}] {msg}", flush=True)


def _refresh_span(day: date) -> str | None:
    """Return a failure message, or None on success."""
    try:
        status = fetch_span(day, config.SPAN_DIR)
    except SpanNotPublished as exc:
        return f"SPAN not published yet: {exc}"
    except Exception as exc:  # noqa: BLE001 - report every failure, do not stop the tape refresh
        return f"SPAN fetch failed: {type(exc).__name__}: {exc}"
    _say(f"SPAN {day}: {status} ({config.SPAN_DIR})")
    return None


def _refresh_tape(day: date) -> list[str]:
    from analytics.structural_tape import available_futures_days
    from data.dhan import backfill_futures_intraday, token_status

    token = token_status()
    if not token.get("present") or token.get("expired") or token.get("malformed"):
        return [f"Dhan token unusable, tape not refreshed: {token}"]
    failures = []
    for sym in TAPE_SYMBOLS:
        try:
            backfill_futures_intraday(sym, log=_say)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{sym} tape backfill failed: {type(exc).__name__}: {exc}")
            continue
        days = available_futures_days(sym)
        latest = days[-1] if days else None
        if latest != day.isoformat():
            failures.append(
                f"{sym} tape ends {latest}, expected {day.isoformat()} "
                "(Dhan returned no new candles; token or market-data lag)"
            )
        else:
            _say(f"{sym} tape ends {latest}")
    return failures


def main(argv: list[str]) -> int:
    from data.holidays import is_trading_day

    if len(argv) > 1:
        print("usage: refresh_liquidation_inputs.py [YYYY-MM-DD]", file=sys.stderr)
        return 2
    day = (
        date.fromisoformat(argv[0])
        if argv
        else datetime.now(ZoneInfo("Asia/Kolkata")).date()
    )
    _say(f"margin-stress inputs for {day}")
    if not is_trading_day(day):
        _say(f"{day} is not an NSE trading day. Nothing to fetch.")
        return 0

    failures = []
    span_failure = _refresh_span(day)
    if span_failure:
        failures.append(span_failure)
    failures.extend(_refresh_tape(day))

    if failures:
        for message in failures:
            _say(f"FAILED: {message}")
        return 1
    _say(f"READY: SPAN and futures tape are both {day}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
