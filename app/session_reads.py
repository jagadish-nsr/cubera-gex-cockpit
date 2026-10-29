"""Load the journals behind the positioning flag and the chop meter.

NSE participant OI is the named book for Nifty and Bank Nifty. Sensex is BSE,
so that file is not applied to it. Futures open interest is the aggregate of
near and next month. Dhan and NSE intraday journals are never mixed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import config
from analytics.choppiness import compute_choppiness_meter
from analytics.positioning_flow import (
    align_clocks,
    burst_notional,
    cvd_window_change,
    live_flow,
    overnight_positioning,
    trailing_futures_change,
)
from app.futures_buildup import (
    FUTURES_INTRADAY_DHAN_DIR,
    FUTURES_INTRADAY_DIR,
    futures_series,
    load_futures_intraday_day,
)
from app.participant_oi import participant_series

IST = timezone(timedelta(hours=5, minutes=30))


def previous_session_date(session: date) -> date:
    """The NSE session before ``session``, skipping weekends and holidays."""
    from data.holidays import is_trading_day
    day = session - timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def _today(now: datetime | None) -> date:
    clock = now or datetime.now(IST)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=IST)
    return clock.astimezone(IST).date()


def _session_with_intraday(symbol: str, session: date) -> str:
    """Today when that file exists. Otherwise the newest captured session.

    After midnight the calendar date has no futures file yet. The last
    session's snapshots are the tape that is actually on disk.
    """
    day = session
    for _ in range(8):
        rows, _source = _pick_intraday(symbol, day.isoformat())
        if rows:
            return day.isoformat()
        day = previous_session_date(day)
    return session.isoformat()


def _pick_intraday(symbol: str, day: str):
    nse = load_futures_intraday_day(day, symbol, FUTURES_INTRADAY_DIR)
    dhan = load_futures_intraday_day(day, symbol, FUTURES_INTRADAY_DHAN_DIR)

    def _rows(frame):
        if frame is None or getattr(frame, "empty", True):
            return []
        return frame.to_dict("records")

    nse_rows, dhan_rows = _rows(nse), _rows(dhan)
    if not nse_rows and not dhan_rows:
        return [], None
    if not nse_rows:
        return dhan_rows, "Dhan"
    if not dhan_rows:
        return nse_rows, "NSE"

    def _end(rows):
        times = [str(r.get("time_ist") or "") for r in rows]
        return max(times) if times else ""

    if _end(dhan_rows) >= _end(nse_rows):
        return dhan_rows, "Dhan"
    return nse_rows, "NSE"


def _bars(symbol: str):
    from sentinel.storage.db import list_recent_bars
    return list_recent_bars(
        f"{symbol}-FUT", timeframe="1m", limit=400,
        session_only=True, _auto_populate=False,
    )


def _bursts(symbol: str, day: str):
    from app.big_money_db import load_events
    try:
        return load_events(symbol, day)
    except Exception:
        return []


def build_session_reads(symbol: str, now: datetime | None = None) -> dict:
    """Positioning flag and chop meter for one cockpit index. No recommendation score."""
    sym = config.index_key(symbol)
    session = _today(now)
    day = _session_with_intraday(sym, session)
    fii = [] if sym == "SENSEX" else participant_series("FII", n=8)
    futures = [] if sym == "SENSEX" else futures_series(sym, n=8)
    overnight = overnight_positioning(fii, futures, symbol=sym)
    rows, source = _pick_intraday(sym, day)
    change = trailing_futures_change(rows)
    if day != session.isoformat() and change.get("window") not in (None, "unavailable"):
        change["window"] = f"{day} {change['window'][0].lower()}{change['window'][1:]}"
    try:
        bars = _bars(sym)
    except Exception:
        bars = []
    bursts = _bursts(sym, day)
    flow_notional = burst_notional(bursts)
    live = live_flow(
        dprice=change["dprice"],
        doi=change["doi"],
        price_now=change["price_now"],
        oi_start=change["oi_start"],
        cvd_change=cvd_window_change(bars),
        buy=flow_notional["buy"],
        sell=flow_notional["sell"],
        window=change["window"],
        symbol=sym,
        source=source,
    )
    file_date = overnight.get("file_date")
    fresh = False
    if file_date:
        fresh = file_date == previous_session_date(session).isoformat()
    aligned = align_clocks(overnight, live, file_is_previous_session=fresh)
    chop = compute_choppiness_meter(bars)
    return {
        "positioning_flow": {
            "alert": aligned["alert"],
            "headline": aligned["headline"],
            "overnight_line": overnight["line"],
            "live_line": live["line"],
            "overnight_state": overnight["state"],
            "live_label": live["label"],
            "file_date": file_date,
            "file_is_previous_session": fresh,
            "tape": live["tape"],
        },
        "choppiness": chop,
    }
