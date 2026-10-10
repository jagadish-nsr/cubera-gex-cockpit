"""sentinel/feed/parser.py — DhanHQ Binary WebSocket Packet Parser & Tick Generator.

Implements Sentinel §6.2 Little-Endian Binary Packet Parser:
  - Header (8 bytes): [feed_code (1B), msg_length (2B), exchange_segment (1B), security_id (4B)]
  - Feed codes:
      * 2: Ticker packet (LTP, LTT)
      * 4: Quote packet (LTP, LTQ, LTT, AvgPrice, Volume, Buy/Sell Qty, Day OHLC)
      * 5: Open Interest packet (OI)
  - Defensive parsing (bounds checking, struct unpacking)
  - Mock Tick Stream generator for offline simulation, unit tests, and non-market hours
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Dhan feed codes
CODE_TICKER = 2
CODE_QUOTE = 4
CODE_OI = 5
CODE_PREV_CLOSE = 6


@dataclass
class Tick:
    security_id: int
    exchange_segment: int
    ltp: float
    ltt: datetime
    ltq: int = 1
    volume: int = 0
    oi: int = 0
    day_open: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    day_close: float = 0.0
    total_buy_qty: float = 0.0
    total_sell_qty: float = 0.0
    feed_code: int = CODE_QUOTE


def parse_dhan_binary_packet(data: bytes) -> Tick | None:
    """Parse a single DhanHQ little-endian binary packet into a Tick.

    Returns Tick or None if packet is malformed.
    """
    if len(data) < 8:
        log.debug("Packet too short: %d bytes (minimum header is 8 bytes)", len(data))
        return None

    try:
        # Header: feed_code (B), msg_length (H), exchange (B), security_id (i)
        feed_code, msg_len, exchange_segment, security_id = struct.unpack_from("<BHBi", data, 0)
        now_utc = datetime.now(timezone.utc)

        if feed_code == CODE_TICKER and len(data) >= 16:
            # Ticker: LTP (f), LTT (I)
            ltp, ltt_sec = struct.unpack_from("<fI", data, 8)
            ltt_dt = datetime.fromtimestamp(ltt_sec, tz=timezone.utc) if ltt_sec > 0 else now_utc
            return Tick(
                security_id=security_id,
                exchange_segment=exchange_segment,
                ltp=round(float(ltp), 2),
                ltt=ltt_dt,
                feed_code=feed_code,
            )

        if feed_code == CODE_QUOTE and len(data) >= 50:
            # Quote packet:
            # ltp (f), ltq (I), ltt (I), avg_price (f), vol (I), buy_qty (d), sell_qty (d), O(f), H(f), L(f), C(f)
            ltp, ltq, ltt_sec, avg_px, vol, buy_qty, sell_qty, o, h, l, c = struct.unpack_from(
                "<fIIfIddffff", data, 8
            )
            ltt_dt = datetime.fromtimestamp(ltt_sec, tz=timezone.utc) if ltt_sec > 0 else now_utc
            return Tick(
                security_id=security_id,
                exchange_segment=exchange_segment,
                ltp=round(float(ltp), 2),
                ltt=ltt_dt,
                ltq=int(ltq),
                volume=int(vol),
                total_buy_qty=float(buy_qty),
                total_sell_qty=float(sell_qty),
                day_open=round(float(o), 2),
                day_high=round(float(h), 2),
                day_low=round(float(l), 2),
                day_close=round(float(c), 2),
                feed_code=feed_code,
            )

        if feed_code == CODE_OI and len(data) >= 12:
            oi, = struct.unpack_from("<I", data, 8)
            return Tick(
                security_id=security_id,
                exchange_segment=exchange_segment,
                ltp=0.0,
                ltt=now_utc,
                oi=int(oi),
                feed_code=feed_code,
            )

        return None
    except Exception as exc:
        log.warning("Error parsing Dhan binary packet (%d bytes): %s", len(data), exc)
        return None


def pack_dhan_quote_packet(
    security_id: int,
    ltp: float,
    volume: int,
    exchange_segment: int = 1,
    ltq: int = 1,
    ltt_sec: int = 0,
    day_open: float = 0.0,
    day_high: float = 0.0,
    day_low: float = 0.0,
    day_close: float = 0.0,
) -> bytes:
    """Helper for testing: pack a synthetic Dhan Quote packet (52 bytes)."""
    header = struct.pack("<BHBi", CODE_QUOTE, 52, exchange_segment, security_id)
    payload = struct.pack(
        "<fIIfIddffff",
        ltp,
        ltq,
        ltt_sec,
        ltp,
        volume,
        float(volume * 0.52),  # buy qty
        float(volume * 0.48),  # sell qty
        day_open or ltp,
        day_high or ltp,
        day_low or ltp,
        day_close or ltp,
    )
    return header + payload
