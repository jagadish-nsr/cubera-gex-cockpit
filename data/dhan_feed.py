"""Dhan v2 LIVE Market-Feed websocket — client + binary packet decoder for the big-money futures
tape ([W2], docs/big-money-tape.md). Distinct from `data/dhan.py`, which is the *historical* REST
client; this is the real-time broadcast feed.

Entitlement + format were verified live 2026-07-21/22 (`scripts/dhan_ws_probe.py`; see the
`dhan-live-ws-feed` memory). Offsets below are LOCKED against real NIFTY-future Quote/Full packets
(the `tests/test_dhan_feed.py` hex fixtures pin them). LITTLE-ENDIAN throughout.

Honest scope (the landmine): the feed is aggregated exchange broadcast — no orders, no participant
identity. We read large PRINTS (LTQ) and best bid/ask (aggressor side, Lee-Ready). Sign-free; a big
print is direction-ambiguous beyond the aggressor tag. Decision-support, never auto-execution.

CREDENTIALS: the access token + client id are read via `data.dhan._secret` and NEVER logged; the
connection URL (which embeds them) is never printed un-masked.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

from data.dhan import DHAN_SEGMENT, _secret, load_scrip_master, near_month_future_id

FEED_URL = "wss://api-feed.dhan.co"
# v2 subscribe request codes (what tier to ask for).
SUB_TICKER, SUB_QUOTE, SUB_FULL = 15, 17, 21
# v2 feed response codes (first byte of each binary packet).
RESP = {2: "Ticker", 4: "Quote", 5: "OI", 6: "PrevClose", 7: "MktStatus", 8: "Full", 50: "Disconnect"}

# --- packet layout (byte offsets, little-endian), verified vs live packets -----------------------
# header (8B): code u8 @0 · msgLen u16 @1 · segment u8 @3 · securityId i32 @4
_HDR = 8
_DEPTH_OFF = 62          # Full packet: 5 depth levels start here
_DEPTH_LEVEL = 20        # bidQty i32 · askQty i32 · bidOrders i16 · askOrders i16 · bidPx f32 · askPx f32


@dataclass
class Tick:
    """One decoded feed packet (fields present depend on the tier). Prices are floats; qty/volume are
    ints (shares-equivalent, i.e. lots × lot_size). bid/ask only come on the Full tier."""
    code: int
    name: str
    security_id: int
    ltp: float | None = None
    ltq: int | None = None            # last-traded quantity of the most recent print
    ltt: int | None = None            # last-trade time (unix seconds)
    atp: float | None = None
    volume: int | None = None         # cumulative day volume
    tot_sell_qty: int | None = None
    tot_buy_qty: int | None = None
    oi: int | None = None
    open: float | None = None
    close: float | None = None        # previous close
    high: float | None = None
    low: float | None = None
    bid: float | None = None          # best bid price (Full)
    ask: float | None = None          # best ask price (Full)


def decode_packet(frame: bytes) -> Tick | None:
    """Decode one binary feed packet into a Tick, or None if too short / unknown code. Offsets are
    LOCKED (see the module docstring); a code we don't model returns a bare Tick with just header."""
    if not frame or len(frame) < _HDR:
        return None
    code = frame[0]
    sec = struct.unpack_from("<I", frame, 4)[0]
    t = Tick(code=code, name=RESP.get(code, f"?{code}"), security_id=sec)
    try:
        if code in (2, 4, 8):                                  # Ticker / Quote / Full share the LTP prefix
            t.ltp = struct.unpack_from("<f", frame, 8)[0]
        if code in (4, 8):                                     # Quote / Full carry the trade + day fields
            t.ltq = struct.unpack_from("<h", frame, 12)[0]
            t.ltt = struct.unpack_from("<i", frame, 14)[0]
            t.atp = struct.unpack_from("<f", frame, 18)[0]
            t.volume = struct.unpack_from("<i", frame, 22)[0]
            t.tot_sell_qty = struct.unpack_from("<i", frame, 26)[0]
            t.tot_buy_qty = struct.unpack_from("<i", frame, 30)[0]
        if code == 4:                                          # Quote: OCHL at 34..
            t.open, t.close, t.high, t.low = struct.unpack_from("<4f", frame, 34)
        if code == 8:                                          # Full: 3× OI ints, then OCHL, then depth
            t.oi = struct.unpack_from("<i", frame, 34)[0]      # (38=OI-day-high, 42=OI-day-low: unused)
            t.open, t.close, t.high, t.low = struct.unpack_from("<4f", frame, 46)
            if len(frame) >= _DEPTH_OFF + _DEPTH_LEVEL:        # best bid/ask = depth level 1
                t.bid = struct.unpack_from("<f", frame, _DEPTH_OFF + 12)[0]
                t.ask = struct.unpack_from("<f", frame, _DEPTH_OFF + 16)[0]
        if code == 5:                                          # OI-only packet
            t.oi = struct.unpack_from("<i", frame, 8)[0]
    except struct.error:
        return t                                               # header still useful; body malformed
    return t


def resolve_futures(symbols=("NIFTY", "BANKNIFTY")) -> dict[int, dict]:
    """Current-expiry future per symbol from the scrip master (`near_month_future_id`), keyed by
    securityId → {symbol, security_id, lot, expiry, segment}. Skips a symbol with no active contract.
    Re-resolve each session — contracts roll (the ids/lot/expiry change). Downloads the scrip master
    ONCE for all symbols."""
    out = {}
    master = load_scrip_master()
    for sym in symbols:
        m = near_month_future_id(sym, master=master)
        if not m:
            continue
        out[int(m["security_id"])] = {"symbol": sym, "security_id": int(m["security_id"]),
                                      "lot": int(m["lot"]), "expiry": m["expiry"],
                                      "segment": DHAN_SEGMENT.get(sym, "NSE_FNO")}
    return out


def _instrument_list(contracts: dict[int, dict]) -> list[dict]:
    return [{"ExchangeSegment": c["segment"], "SecurityId": str(c["security_id"])}
            for c in contracts.values()]


def connect(contracts: dict[int, dict], tiers=(SUB_FULL,), timeout: int = 12):
    """Open the v2 feed socket and subscribe ``contracts`` at each tier in ``tiers`` (default Full →
    LTP+LTQ+volume+OI+bid/ask). Returns the live websocket. Raises RuntimeError with a clear message
    on a missing credential; lets websocket's own exceptions (e.g. WebSocketBadStatusException on a
    rejected handshake = entitlement/auth failure) propagate to the caller. Token NEVER logged."""
    import websocket  # websocket-client

    token = _secret("DHAN_ACCESS_TOKEN", ".dhan_token")
    client = _secret("DHAN_CLIENT_ID", ".dhan_client_id")
    if not token:
        raise RuntimeError("No Dhan access token (DHAN_ACCESS_TOKEN / data/.dhan_token).")
    if not client:
        raise RuntimeError("Dhan v2 feed needs a client id (DHAN_CLIENT_ID / data/.dhan_client_id).")
    url = f"{FEED_URL}?version=2&token={token}&clientId={client}&authType=2"
    ws = websocket.create_connection(url, timeout=timeout)
    instruments = _instrument_list(contracts)
    for code in tiers:
        ws.send(json.dumps({"RequestCode": code, "InstrumentCount": len(instruments),
                            "InstrumentList": instruments}))
    return ws
