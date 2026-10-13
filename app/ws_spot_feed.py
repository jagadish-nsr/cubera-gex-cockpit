"""WebSocket-powered spot + near-futures tick feed — replaces the 3s REST poll with exchange-push
real-time ticks for NIFTY, BANKNIFTY and SENSEX.

Architecture
────────────
One persistent background thread opens a single Dhan v2 WebSocket (``wss://api-feed.dhan.co``)
and subscribes to:
  • Index spot underlyings  → IDX_I segment  (NIFTY:13, BANKNIFTY:25, SENSEX:51) — Quote tier
  • Near-month index futures → NSE_FNO / BSE_FNO               — Quote tier (gives OHLC + LTQ + OI)

Every decoded ``Quote`` packet is written directly into ``app.spot_ticker._STORE`` via
``spot_ticker.push_ws_tick()``.  The REST poller in ``spot_ticker._loop`` keeps running as the
*fallback*: when the socket is live the REST value is just a background warmup; when the socket
drops the REST value stays fresh.

Data flow (outside → in)
────────────────────────
  Exchange → wss (binary Quote packet) → decode_packet() → push_ws_tick()
                                                               ↓
                                              _STORE[symbol]["ohlc"]  ← spot fields (IDX_I ticks)
                                              _STORE[symbol]["fut"]   ← futures price/OI (FNO ticks)
                                              _STORE[symbol]["quote"] ← _compile_quote() result
                                              _STORE[symbol]["ws_at"] ← epoch of last WS tick

VIX:  India VIX (IDX_I:21) is also subscribed; its LTP is patched into the current ohlc["vix"]
      dict without overwriting the OHLC fields that came from the NIFTY/BANKNIFTY/SENSEX ticks.

Status / health
───────────────
``get_status()`` returns a small dict the dashboard can surface: ws_connected, ws_symbol_ticks,
last_tick_age_s.  ``is_ws_live()`` returns True when the socket is connected AND a tick arrived
in the last 10 seconds.

Lifecycle
─────────
``ensure_running()`` is idempotent — safe to call on every Streamlit repaint; the thread is
started once per process. The thread reconnects automatically after any drop (a 24h token expiry
or a network hiccup).  ``stop()`` asks it to exit cleanly.

Constraints
───────────
• Credentials (token, clientId) are NEVER logged — the WS URL is masked in the status output.
• The ``IDX_I`` WebSocket Quote tier delivers LTP + OHLC + prev_close for index spots — verified
  live 2026-10-08 (see the inline probe: ``python scripts/dhan_ws_probe.py``).
• Outside market hours (09:15–15:30 IST on weekdays) the socket connects fine but trade ticks
  stop; ``is_ws_live()`` returns False after 10s of silence, letting the REST poller take over.
"""

from __future__ import annotations

import json
import struct
import threading
import time
from datetime import datetime, timedelta, timezone

# ── Dhan WebSocket constants ────────────────────────────────────────────────────────────────────
_FEED_URL = "wss://api-feed.dhan.co"
_SUB_QUOTE = 17                        # v2 subscribe code for Quote tier (LTP + LTQ + OHLC)
_RESP_QUOTE = 4                        # v2 receive code for a Quote packet
_RESP_TICKER = 2                       # v2 receive code for a Ticker packet (LTP only)
_HEADER_LEN = 8                        # bytes in the fixed header

# Index spot security IDs on IDX_I segment
_SPOT_SCRIPS: dict[str, int] = {
    "NIFTY":     13,
    "BANKNIFTY": 25,
    "SENSEX":    51,
}
_VIX_SCRIP = 21           # India VIX — IDX_I
_IDX_SEG = "IDX_I"

# ── Process-global state ────────────────────────────────────────────────────────────────────────
_STATUS: dict = {
    "ws_connected": False,
    "ws_error": None,
    "ws_symbol_ticks": {},     # symbol → int tick count
    "last_tick_epoch": None,
    "started_at": None,
}
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()

# Map FNO-segment security_id → {symbol, expiry} for futures ticks
# Populated lazily once per session day by _resolve_futures_map()
_FUT_MAP: dict[int, dict] = {}          # security_id → {symbol, expiry, segment}
_FUT_MAP_DAY: str | None = None         # "YYYY-MM-DD" when the map was resolved
_FUT_MAP_LOCK = threading.Lock()

# Map FNO-segment security_id → {symbol, strike, side, expiry, segment} for options ticks
_OPT_MAP: dict[int, dict] = {}          # security_id → {symbol, strike, side, expiry, segment}
_OPT_MAP_LOCK = threading.Lock()
_SUBSCRIBED_OPT_IDS: set[int] = set()
_PENDING_OPT_SUBS: list[dict] = []

IST = timezone(timedelta(hours=5, minutes=30))


def subscribe_options(symbol: str, strikes, expiry_iso: str | None = None) -> list[int]:
    """Dynamically register and subscribe option contracts on the WebSocket feed.
    Resolves security IDs from the cached scrip master for the given symbol and strikes.
    Thread-safe; queues subscriptions for the running WS loop."""
    try:
        from data.dhan_live import _option_contracts
        c = _option_contracts(symbol, expiry_iso)
        if not c.get("seg") or not c.get("by_strike"):
            return []

        seg = c["seg"]
        added_ids = []
        with _OPT_MAP_LOCK:
            for k in strikes:
                pair = c["by_strike"].get(float(k)) or {}
                for side in ("CE", "PE"):
                    sid = pair.get(side)
                    if sid:
                        _OPT_MAP[sid] = {
                            "symbol": symbol.upper(),
                            "strike": float(k),
                            "side": side,
                            "expiry": c.get("expiry"),
                            "segment": seg,
                        }
                        if sid not in _SUBSCRIBED_OPT_IDS:
                            _PENDING_OPT_SUBS.append({"ExchangeSegment": seg, "SecurityId": str(sid)})
                            added_ids.append(sid)
        return added_ids
    except Exception:
        return []


# ── Packet decoder (same binary layout as data/dhan_feed.decode_packet) ─────────────────────────

def _decode_quote(frame: bytes) -> dict | None:
    """Decode a Dhan v2 Quote (code 4) or Ticker (code 2) binary packet.
    Returns a plain dict or None if the frame is too short/malformed."""
    if not frame or len(frame) < _HEADER_LEN:
        return None
    code = frame[0]
    if code not in (_RESP_TICKER, _RESP_QUOTE):
        return None
    try:
        sec = struct.unpack_from("<I", frame, 4)[0]
        out: dict = {"code": code, "security_id": sec}
        if len(frame) >= 12:
            out["ltp"] = struct.unpack_from("<f", frame, 8)[0]
        if code == _RESP_QUOTE and len(frame) >= 50:
            # ltq @12 (h=int16), ltt @14, atp @18, volume @22, sell_qty @26, buy_qty @30
            out["ltq"] = struct.unpack_from("<h", frame, 12)[0]
            out["ltt"] = struct.unpack_from("<i", frame, 14)[0]
            out["atp"] = struct.unpack_from("<f", frame, 18)[0]
            out["volume"] = struct.unpack_from("<i", frame, 22)[0]
            # OHLC: open @34, close(prev_close) @38, high @42, low @46
            out["open"], out["prev_close"], out["high"], out["low"] = struct.unpack_from("<4f", frame, 34)
        return out
    except struct.error:
        return None


# ── Futures contract resolver ────────────────────────────────────────────────────────────────────

def _resolve_futures_map(symbols=("NIFTY", "BANKNIFTY", "SENSEX")) -> dict[int, dict]:
    """Build security_id → {symbol, expiry, segment} for the NEAR futures of each symbol,
    resolved from the scrip master.  Cached per session day."""
    global _FUT_MAP, _FUT_MAP_DAY
    today = datetime.now(IST).date().isoformat()
    with _FUT_MAP_LOCK:
        if _FUT_MAP_DAY == today and _FUT_MAP:
            return _FUT_MAP
        try:
            from data.dhan import load_scrip_master
            import pandas as pd
            from datetime import date
            df = load_scrip_master()
            if df is None:
                return {}
            out: dict[int, dict] = {}
            for sym in symbols:
                f = df[
                    (df["SEM_INSTRUMENT_NAME"] == "FUTIDX") &
                    (df["SEM_TRADING_SYMBOL"].astype(str).str.upper().str.startswith(sym.upper() + "-"))
                ].copy()
                f["exp"] = pd.to_datetime(f["SEM_EXPIRY_DATE"], errors="coerce")
                f = f[f["exp"].dt.date >= date.today()].sort_values("exp")
                if not f.empty:
                    r = f.iloc[0]
                    seg = f"{str(r['SEM_EXM_EXCH_ID']).upper()}_FNO"
                    out[int(r["SEM_SMST_SECURITY_ID"])] = {
                        "symbol": sym,
                        "expiry": r["exp"].date().isoformat(),
                        "segment": seg,
                    }
            _FUT_MAP = out
            _FUT_MAP_DAY = today
            return out
        except Exception:  # noqa: BLE001
            return {}


def _build_instrument_list(spot_ids: dict[str, int], fut_map: dict[int, dict]) -> list[dict]:
    """One flat subscription list: IDX_I spots (+ VIX) + FNO futures + active FNO options."""
    instr = [{"ExchangeSegment": _IDX_SEG, "SecurityId": str(sid)}
             for sid in list(spot_ids.values()) + [_VIX_SCRIP]]
    instr += [{"ExchangeSegment": c["segment"], "SecurityId": str(sid)}
              for sid, c in fut_map.items()]
    with _OPT_MAP_LOCK:
        for sid, c in _OPT_MAP.items():
            instr.append({"ExchangeSegment": c["segment"], "SecurityId": str(sid)})
    return instr


# ── Tick handlers ─────────────────────────────────────────────────────────────────────────────────

def _handle_spot_tick(pkt: dict, spot_ids: dict[str, int]) -> None:
    """Push a spot IDX_I tick into spot_ticker._STORE."""
    import app.spot_ticker as _st

    sec = pkt["security_id"]
    ltp = pkt.get("ltp")
    if ltp is None or ltp <= 0:
        return

    # VIX tick — patch vix sub-dict across all symbols
    if sec == _VIX_SCRIP:
        open_px = pkt.get("open") or ltp
        chg = (ltp - open_px) if open_px else 0.0
        pct = (chg / open_px * 100.0) if open_px else 0.0
        vix_dict = {"last": ltp, "change": chg, "pct_change": pct}
        for sym in spot_ids:
            rec = _st._STORE.get(sym)
            if rec and rec.get("ohlc"):
                rec["ohlc"]["vix"] = vix_dict
        return

    sym = next((k for k, v in spot_ids.items() if v == sec), None)
    if sym is None:
        return

    ohlc = {
        "ltp":        ltp,
        "open":       pkt.get("open") or ltp,
        "high":       pkt.get("high") or ltp,
        "low":        pkt.get("low") or ltp,
        "prev_close": pkt.get("prev_close") or ltp,
        "as_of":      datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
    }
    # Preserve existing VIX so it's not wiped on next spot tick
    existing_ohlc = (_st._STORE.get(sym) or {}).get("ohlc") or {}
    if existing_ohlc.get("vix"):
        ohlc["vix"] = existing_ohlc["vix"]

    _st.push_ws_tick(sym, ohlc=ohlc)
    _STATUS["ws_symbol_ticks"][sym] = _STATUS["ws_symbol_ticks"].get(sym, 0) + 1
    _STATUS["last_tick_epoch"] = int(time.time())


def _handle_fut_tick(pkt: dict, fut_map: dict[int, dict]) -> None:
    """Push a near-futures tick into spot_ticker._STORE."""
    import app.spot_ticker as _st

    sec = pkt["security_id"]
    ltp = pkt.get("ltp")
    if ltp is None or ltp <= 0:
        return

    info = fut_map.get(sec)
    if info is None:
        return

    sym = info["symbol"]
    existing_ohlc = (_st._STORE.get(sym) or {}).get("ohlc") or {}
    spot = existing_ohlc.get("ltp") or ltp

    fut_open = pkt.get("open") or ltp
    chg = (ltp - fut_open) if fut_open else 0.0

    fut = {
        "symbol":  sym,
        "expiry":  info["expiry"],
        "price":   ltp,
        "change":  chg,
        "oi":      pkt.get("oi"),
        "volume":  pkt.get("volume"),
        "spot":    spot,
    }
    _st.push_ws_tick(sym, fut=fut)
    _STATUS["ws_symbol_ticks"][f"{sym}-FUT"] = _STATUS["ws_symbol_ticks"].get(f"{sym}-FUT", 0) + 1
    _STATUS["last_tick_epoch"] = int(time.time())

    # Forward live tick directly to OrderFlow Sentinel
    try:
        from sentinel.feed.live import push_live_tick_to_sentinel
        push_live_tick_to_sentinel(pkt, sym)
    except Exception:
        pass


def _handle_opt_tick(pkt: dict, info: dict) -> None:
    """Push an option contract tick into spot_ticker._STORE_OPTIONS."""
    import app.spot_ticker as _st
    _st.push_ws_option_tick(info["symbol"], info["strike"], info["side"], pkt)
    opt_key = f"{info['symbol']}-{int(info['strike'])}-{info['side']}"
    _STATUS["ws_symbol_ticks"][opt_key] = _STATUS["ws_symbol_ticks"].get(opt_key, 0) + 1
    _STATUS["last_tick_epoch"] = int(time.time())


# ── Main WS run loop ─────────────────────────────────────────────────────────────────────────────

def _run(symbols: tuple[str, ...]) -> None:
    """Background daemon thread: connect → subscribe → decode → push. Reconnects on any drop."""
    import websocket  # websocket-client

    spot_ids = {k: v for k, v in _SPOT_SCRIPS.items() if k in symbols}
    _STATUS["started_at"] = time.time()

    while not _STOP.is_set():
        # Re-resolve futures map each reconnect attempt (handles daily roll)
        fut_map = _resolve_futures_map(symbols)
        all_instr = _build_instrument_list(spot_ids, fut_map)

        if not all_instr:
            _STATUS["ws_error"] = "no instruments resolved — retrying in 30s"
            _STATUS["ws_connected"] = False
            _STOP.wait(30)
            continue

        with _OPT_MAP_LOCK:
            for it in all_instr:
                if it["ExchangeSegment"] in ("NSE_FNO", "BSE_FNO"):
                    try:
                        _SUBSCRIBED_OPT_IDS.add(int(it["SecurityId"]))
                    except Exception:
                        pass

        import config
        if not config.is_market_open():
            _STATUS["ws_connected"] = False
            _STATUS["ws_error"] = "Market closed (FnO hours: 09:15 AM – 03:40 PM IST)"
            _STOP.wait(15)
            continue

        try:
            from data.dhan import _secret
            token = _secret("DHAN_ACCESS_TOKEN", ".dhan_token")
            client = _secret("DHAN_CLIENT_ID", ".dhan_client_id")
            if not token or not client:
                _STATUS["ws_error"] = "missing Dhan credentials — retrying in 30s"
                _STATUS["ws_connected"] = False
                _STOP.wait(30)
                continue

            url = f"{_FEED_URL}?version=2&token={token}&clientId={client}&authType=2"
            ws = websocket.create_connection(url, timeout=12)
        except RuntimeError as e:
            _STATUS["ws_error"] = str(e)
            _STATUS["ws_connected"] = False
            _STOP.wait(30)
            continue
        except Exception as e:  # noqa: BLE001
            _STATUS["ws_error"] = f"connect: {type(e).__name__}"
            _STATUS["ws_connected"] = False
            _STOP.wait(5)
            continue

        try:
            ws.send(json.dumps({
                "RequestCode": _SUB_QUOTE,
                "InstrumentCount": len(all_instr),
                "InstrumentList": all_instr,
            }))
        except Exception as e:  # noqa: BLE001
            _STATUS["ws_error"] = f"subscribe: {type(e).__name__}"
            _STATUS["ws_connected"] = False
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
            _STOP.wait(5)
            continue

        _STATUS["ws_connected"] = True
        _STATUS["ws_error"] = None
        ws.settimeout(1.0)

        spot_sec_set = set(spot_ids.values()) | {_VIX_SCRIP}

        try:
            while not _STOP.is_set():
                if not config.is_market_open():
                    _STATUS["ws_error"] = "Market closed (FnO hours: 09:15 AM – 03:40 PM IST)"
                    break
                # Process dynamic option subscriptions queued from the UI / analytics
                if _PENDING_OPT_SUBS:
                    with _OPT_MAP_LOCK:
                        to_sub = list(_PENDING_OPT_SUBS)
                        _PENDING_OPT_SUBS.clear()
                    if to_sub:
                        try:
                            ws.send(json.dumps({
                                "RequestCode": _SUB_QUOTE,
                                "InstrumentCount": len(to_sub),
                                "InstrumentList": to_sub,
                            }))
                            for item in to_sub:
                                _SUBSCRIBED_OPT_IDS.add(int(item["SecurityId"]))
                        except Exception:
                            pass

                try:
                    opcode, frame = ws.recv_data()
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:  # noqa: BLE001
                    break

                if opcode == websocket.ABNF.OPCODE_CLOSE:
                    break

                if not isinstance(frame, (bytes, bytearray)) or len(frame) < _HEADER_LEN:
                    continue

                pkt = _decode_quote(bytes(frame))
                if pkt is None:
                    continue

                sec = pkt.get("security_id", 0)
                if sec in spot_sec_set:
                    _handle_spot_tick(pkt, spot_ids)
                elif sec in fut_map:
                    _handle_fut_tick(pkt, fut_map)
                elif sec in _OPT_MAP:
                    _handle_opt_tick(pkt, _OPT_MAP[sec])

        except Exception:  # noqa: BLE001
            pass
        finally:
            _STATUS["ws_connected"] = False
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

        if not _STOP.is_set():
            _STOP.wait(3)   # brief pause before reconnect


# ── Public API ───────────────────────────────────────────────────────────────────────────────────

def ensure_running(symbols: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")) -> None:
    """Start the WS feed thread if it isn't already alive. Idempotent — safe on every Streamlit
    repaint. The thread is a daemon so it dies when the process exits."""
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(
            target=_run,
            args=(symbols,),
            name="ws-spot-feed",
            daemon=True,
        )
        _THREAD.start()


def stop() -> None:
    """Ask the WS feed thread to exit cleanly. Safe to call multiple times."""
    _STOP.set()


def is_ws_live(max_silence_s: float = 10.0) -> bool:
    """True when the socket is connected AND a tick arrived within ``max_silence_s`` seconds
    (trade ticks stop outside 09:15–15:30 IST, so we detect market-hours gap)."""
    last = _STATUS.get("last_tick_epoch")
    if last is None:
        return False
    return _STATUS.get("ws_connected", False) and (time.time() - last) < max_silence_s


def get_status() -> dict:
    """Snapshot of feed health for the dashboard status bar."""
    last = _STATUS.get("last_tick_epoch")
    return {
        "ws_connected": _STATUS.get("ws_connected", False),
        "ws_error": _STATUS.get("ws_error"),
        "ws_symbol_ticks": dict(_STATUS.get("ws_symbol_ticks", {})),
        "last_tick_age_s": round(time.time() - last, 1) if last else None,
        "is_live": is_ws_live(),
    }
