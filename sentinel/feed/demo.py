"""sentinel/feed/demo.py — Sentinel tape loader for the production book.

Loads bars from the live feed or the last EOD snapshot. This process does not
generate candles or signals. A replay or simulator, when it exists, runs as a
separate application and does not write this database.
"""

from __future__ import annotations

import logging
import re

from sentinel.storage.db import (
    save_signal,
    list_recent_bars,
    list_recent_signals,
    ensure_sentinel_schema,
)


def _update_signal_walls_in_place(instrument_id: str, call_wall: float, put_wall: float) -> None:
    """Non-destructive update of GEX wall references in existing signals when walls shift."""
    try:
        existing_sigs = list_recent_signals(instrument_id, limit=30)
        for sig in existing_sigs:
            upd = False
            txt = sig.insight_text
            if put_wall and "Put Wall " in txt:
                m = re.search(r"Put Wall ([\d,]+)", txt)
                if m and abs(float(m.group(1).replace(",", "")) - put_wall) >= 25.0:
                    txt = re.sub(r"Put Wall [\d,]+", f"Put Wall {put_wall:,.0f}", txt)
                    upd = True
            if call_wall and "Call Wall " in txt:
                m = re.search(r"Call Wall ([\d,]+)", txt)
                if m and abs(float(m.group(1).replace(",", "")) - call_wall) >= 25.0:
                    txt = re.sub(r"Call Wall [\d,]+", f"Call Wall {call_wall:,.0f}", txt)
                    upd = True
            if upd:
                sig.insight_text = txt
                save_signal(sig)
    except Exception:
        pass


log = logging.getLogger(__name__)


def _load_from_eod_snapshot(
    instrument_id: str,
    symbol: str,
    call_wall: float | None = None,
    put_wall: float | None = None,
) -> bool:
    """Redirect to sentinel.feed.history — canonical implementation moved there.

    Kept here for backward-compatibility with existing callers and tests.
    All derived metrics (CVD, VWAP bands, buy/sell split) are computed in
    history.py on authentic OHLCV data.
    """
    from sentinel.feed.history import _load_from_eod_snapshot as _hist_load
    return _hist_load(instrument_id, symbol=symbol, call_wall=call_wall, put_wall=put_wall)


def seed_sentinel_demo_if_needed(
    instrument_id: str = "NIFTY-FUT",
    spot: float = 22680.0,
    call_wall: float = 22800.0,
    put_wall: float = 22500.0,
    force: bool = False,
) -> None:
    """Load sentinel bars from the live feed, else the last EOD snapshot.

    `spot` is accepted so existing callers keep working. It is not used to
    invent a session. When neither source has bars, the database is left unchanged.
    """
    ensure_sentinel_schema()
    sym = instrument_id.replace("-FUT", "")

    try:
        from sentinel.feed.live import sync_live_sentinel_if_needed
        synced = sync_live_sentinel_if_needed(
            instrument_id, symbol=sym, call_wall=call_wall, put_wall=put_wall, force=force
        )
        if synced:
            return
    except Exception as exc:
        log.warning("Live sentinel WS/API sync failed: %s", exc)

    existing_1m = list_recent_bars(instrument_id, timeframe="1m", limit=10, session_only=False, _auto_populate=False)
    if existing_1m and not force:
        _update_signal_walls_in_place(instrument_id, call_wall, put_wall)
        return

    if _load_from_eod_snapshot(instrument_id, symbol=sym, call_wall=call_wall, put_wall=put_wall):
        log.info("Loaded authentic EOD snapshot data for %s", instrument_id)
        return

    if existing_1m:
        return

    log.warning(
        "No live feed or EOD snapshot for %s (requested spot %.1f). No bars were written.",
        instrument_id,
        float(spot or 0),
    )
