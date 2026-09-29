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

from data.dhan import DHAN_SEGMENT, _secret, load_scrip_master, nearest_futures

ROLL_NAMES = ("near", "next", "far")

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


def resolve_futures(symbols=("NIFTY", "BANKNIFTY", "SENSEX"), rolls: int = 2) -> dict[int, dict]:
    """The ``rolls`` nearest futures PER symbol from the scrip master (near + next month by default),
    keyed by securityId → {symbol, roll, security_id, lot, expiry, segment}. ``roll`` is 'near'/'next'.
    Watching both means the expiry roll doesn't hide the big money migrating to next-month. Skips a
    symbol with no active contract. Re-resolve each session — contracts roll. One scrip-master download."""
    out = {}
    master = load_scrip_master()
    for sym in symbols:
        for i, m in enumerate(nearest_futures(sym, n=rolls, master=master)):
            out[int(m["security_id"])] = {
                "symbol": sym, "roll": ROLL_NAMES[i] if i < len(ROLL_NAMES) else f"r{i}",
                "security_id": int(m["security_id"]), "lot": int(m["lot"]),
                "expiry": m["expiry"], "segment": DHAN_SEGMENT.get(sym, "NSE_FNO")}
    return out


def _option_rows(symbol: str, master=None):
    """Non-expired OPTIDX rows for ``symbol`` from the scrip master, with a parsed expiry date column."""
    import pandas as pd
    from datetime import date
    df = master if master is not None else load_scrip_master()
    if df is None:
        return None
    o = df[(df["SEM_INSTRUMENT_NAME"] == "OPTIDX")
           & (df["SEM_TRADING_SYMBOL"].astype(str).str.startswith(f"{symbol}-"))].copy()
    if o.empty:
        return o
    o["exp"] = pd.to_datetime(o["SEM_EXPIRY_DATE"], errors="coerce").dt.date
    return o[o["exp"] >= date.today()]


def resolve_option(symbol: str, strike: float, side: str, expiry: str | None = None, master=None):
    """One option contract → {symbol, security_id, strike, side, expiry, lot, segment} for the Dhan WS
    feed, or None. ``side`` is 'CE'/'PE'; ``strike`` is the absolute strike; ``expiry`` (ISO date) defaults
    to the NEAREST non-expired expiry (the 0-DTE/weekly the gamma-blast watches). Exact strike match."""
    o = _option_rows(symbol, master)
    if o is None or o.empty:
        return None
    side = side.upper()
    o = o[o["SEM_OPTION_TYPE"].astype(str).str.upper() == side]
    if expiry:
        from datetime import date as _date
        want = expiry if isinstance(expiry, str) else expiry.isoformat()
        o = o[o["exp"].astype(str) == want]
    else:
        nearest = min(o["exp"]) if len(o) else None            # nearest non-expired expiry
        o = o[o["exp"] == nearest]
    o = o[o["SEM_STRIKE_PRICE"].astype(float) == float(strike)]
    if o.empty:
        return None
    r = o.iloc[0]
    return {"symbol": symbol, "security_id": int(r["SEM_SMST_SECURITY_ID"]),
            "strike": float(r["SEM_STRIKE_PRICE"]), "side": side,
            "expiry": r["exp"].isoformat(), "lot": int(r["SEM_LOT_UNITS"]),
            "segment": DHAN_SEGMENT.get(symbol, "NSE_FNO")}


def resolve_blast_pair(symbol: str, spot: float, expiry: str | None = None, master=None) -> dict | None:
    """The gamma-blast anchor pair: the ATM CALL + PUT at the listed strike NEAREST ``spot`` (the 13:45
    ATM the blast fixes), for the nearest (or given) expiry. Returns {strike, expiry, call:{…}, put:{…}}
    (each side the resolve_option dict) so the WS lane can subscribe exactly those two contracts. None if
    no strikes are listed. Snapping to a real listed strike mirrors app.gammablast.atm_strike."""
    o = _option_rows(symbol, master)
    if o is None or o.empty:
        return None
    if expiry:
        want = expiry if isinstance(expiry, str) else expiry.isoformat()
        o = o[o["exp"].astype(str) == want]
    else:
        o = o[o["exp"] == min(o["exp"])]
    if o.empty:
        return None
    strikes = sorted({float(k) for k in o["SEM_STRIKE_PRICE"] if k == k})
    if not strikes:
        return None
    atm = min(strikes, key=lambda k: abs(k - float(spot)))     # nearest listed strike to spot
    exp = o["exp"].iloc[0].isoformat()
    call = resolve_option(symbol, atm, "CE", exp, master=o)
    put = resolve_option(symbol, atm, "PE", exp, master=o)
    if not (call and put):
        return None
    return {"symbol": symbol, "strike": atm, "expiry": exp, "call": call, "put": put}


def resolve_blast_set(symbol: str, spot: float, expiry: str | None = None, master=None) -> dict | None:
    """The blast anchor pair PLUS the two continuation WALLS: the CALL at the next strike ABOVE (the wall
    an up-cross must overrun) and the PUT at the next strike BELOW (the down-cross wall). Since the cross
    direction isn't known at 13:45, both are captured; the scorer uses whichever matches the realised
    direction. Returns {symbol, strike, step, expiry, call, put, wall_up, wall_dn} — each leg a
    resolve_option dict tagged with ``role`` (anchor/wall_up/wall_dn). wall_* is None at a grid edge."""
    pair = resolve_blast_pair(symbol, spot, expiry, master)
    if not pair:
        return None
    o = _option_rows(symbol, master)
    exp = pair["expiry"]
    o = o[o["exp"].astype(str) == exp]
    strikes = sorted({float(k) for k in o["SEM_STRIKE_PRICE"] if k == k})
    atm = pair["strike"]
    i = strikes.index(atm)
    up_k = strikes[i + 1] if i + 1 < len(strikes) else None
    dn_k = strikes[i - 1] if i - 1 >= 0 else None
    step = (min((strikes[j + 1] - strikes[j] for j in range(len(strikes) - 1)))
            if len(strikes) > 1 else None)
    call = {**pair["call"], "role": "anchor"}
    put = {**pair["put"], "role": "anchor"}
    wall_up = resolve_option(symbol, up_k, "CE", exp, master=o) if up_k is not None else None
    wall_dn = resolve_option(symbol, dn_k, "PE", exp, master=o) if dn_k is not None else None
    if wall_up:
        wall_up["role"] = "wall_up"
    if wall_dn:
        wall_dn["role"] = "wall_dn"
    return {"symbol": symbol, "strike": atm, "step": step, "expiry": exp,
            "call": call, "put": put, "wall_up": wall_up, "wall_dn": wall_dn}


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
