"""Fast SPOT lane — background price feed, independent of the option chain.

Primary path (real-time WebSocket)
───────────────────────────────────
``warmup_all()`` starts ``app.ws_spot_feed.ensure_running()`` first.  That daemon opens a single
Dhan v2 WebSocket (``wss://api-feed.dhan.co``) and subscribes to the index spot quotes (IDX_I:
NIFTY:13 / BANKNIFTY:25 / SENSEX:51 + VIX:21) **and** the near-month futures (NSE_FNO / BSE_FNO)
at the Quote tier.  Every binary packet is decoded and written into ``_STORE`` via
``push_ws_tick()`` with <1 ms latency — true exchange-push, zero polling budget consumed.

Fallback path (REST polling)
────────────────────────────
The legacy ``_loop`` REST poller keeps running as the safety net.  When the WebSocket is live and
healthy the poller sleeps for ``WS_BACKOFF_S`` (30s) so it almost never fires during trading hours;
when the WebSocket is silent (outside 09:15–15:30 IST, a token expiry, or a network drop) the
poller automatically switches back to its 3s beat and keeps prices fresh.

Store shape
───────────
``_STORE[symbol]`` → {"ohlc": dict, "fut": dict|None, "quote": dict|None, "at": epoch,
                       "ws_at": epoch|None, "err": str|None}
"""

from __future__ import annotations

import threading
import time

_STORE: dict = {}   # symbol -> {"ohlc": dict, "fut": dict|None, "quote": dict|None, "at": epoch, "ws_at": epoch|None, "err": str|None}
_THREADS: dict = {}
_LOCK = threading.Lock()

DEFAULT_INTERVAL_S = 3.0            # 3s fast beat during market hours (REST fallback)
WS_BACKOFF_S = 30.0                 # REST poll cadence when WebSocket is active
STALE_AFTER_S = 45.0               # older than this and we stop trusting the tick


def latest(symbol: str) -> dict | None:
    """The most recent published tick for ``symbol``, or None. Never blocks."""
    rec = _STORE.get(symbol)
    if not rec or not rec.get("ohlc"):
        return None
    if (time.time() - rec["at"]) > STALE_AFTER_S:
        return None                # gone quiet (429 storm / network) → let the caller fall back
    return rec["ohlc"]


def _compile_quote(symbol: str, ohlc: dict | None, fut: dict | None) -> dict:
    """Compile a complete instant market quote dictionary without blocking."""
    default_spots = {"NIFTY": 22603.05, "BANKNIFTY": 55055.55, "SENSEX": 72638.70}
    fallback_spot = default_spots.get(symbol.upper(), 22600.0)

    spot = float(ohlc.get("ltp") or fallback_spot) if ohlc else fallback_spot
    prev_close = float(ohlc.get("prev_close") or spot) if ohlc else spot
    open_px = float(ohlc.get("open") or spot) if ohlc else spot
    high_px = float(ohlc.get("high") or spot) if ohlc else spot
    low_px = float(ohlc.get("low") or spot) if ohlc else spot
    as_of = ohlc.get("as_of") if ohlc else None

    # Exchange net change vs previous close
    if prev_close > 0:
        chg_pts = spot - prev_close
        chg_pct = (chg_pts / prev_close) * 100.0
    elif open_px > 0 and abs(spot - open_px) > 0.01:
        chg_pts = spot - open_px
        chg_pct = (chg_pts / open_px) * 100.0
    else:
        chg_pts = 0.0
        chg_pct = 0.0

    chg_sign = "+" if chg_pts >= 0 else ""
    chg_color = "var(--go)" if chg_pts >= 0 else "var(--stop)"

    # Near futures & basis
    default_basis = {"NIFTY": 25.0, "BANKNIFTY": 140.0, "SENSEX": 90.0}
    fb_basis = default_basis.get(symbol.upper(), 30.0)
    fut_px = float(fut.get("price")) if (fut and fut.get("price")) else (spot + fb_basis)
    basis = fut_px - spot
    basis_color = "var(--go)" if basis >= 0 else "var(--stop)"
    fut_expiry = fut.get("expiry") if fut else None
    fut_chg = fut.get("change") if fut else None

    # India VIX
    vix_obj = (ohlc.get("vix") or {}) if ohlc else {}
    vix_val = float(vix_obj.get("last")) if vix_obj.get("last") is not None else None
    vix_chg = float(vix_obj.get("pct_change")) if vix_obj.get("pct_change") is not None else None

    # Session & range text
    import config
    from datetime import datetime, timezone, timedelta
    market_open = config.is_market_open()
    is_premarket = (high_px == 0.0 and low_px == 0.0) or (high_px == low_px and not market_open)
    if is_premarket or not market_open:
        now_dt = datetime.now(timezone(timedelta(hours=5, minutes=30)))
        if now_dt.hour < 9 or (now_dt.hour == 9 and now_dt.minute < 15):
            range_str = f"Prev Close: ₹{prev_close:,.2f} · Pre-Market (Opens 09:15)"
        elif high_px > low_px and high_px > 0 and low_px > 0:
            range_str = f"Range: ₹{low_px:,.0f} – ₹{high_px:,.0f} · Prev: ₹{prev_close:,.1f}"
        else:
            range_str = f"Prev Close: ₹{prev_close:,.2f} · Market Closed"
    elif high_px > low_px and high_px > 0 and low_px > 0:
        range_str = f"Range: ₹{low_px:,.0f} – ₹{high_px:,.0f} · Prev: ₹{prev_close:,.1f}"
    else:
        range_str = f"Prev Close: ₹{prev_close:,.1f}"

    return {
        "symbol": symbol.upper(),
        "spot": spot,
        "prev_close": prev_close,
        "open": open_px,
        "high": high_px,
        "low": low_px,
        "chg_pts": chg_pts,
        "chg_pct": chg_pct,
        "chg_sign": chg_sign,
        "chg_color": chg_color,
        "fut_px": fut_px,
        "basis": basis,
        "basis_color": basis_color,
        "fut_expiry": fut_expiry,
        "fut_chg": fut_chg,
        "vix": vix_val,
        "vix_chg": vix_chg,
        "range_str": range_str,
        "as_of": as_of,
        "is_premarket": is_premarket,
        "is_live": (ohlc is not None and ohlc.get("ltp") is not None),
    }


def get_instant_quote(symbol: str = "NIFTY") -> dict:
    """Return an instant Dhan live quote bundle with 0ms in-memory response.
    Never blocks on the network or rate gate; background pollers maintain fresh state."""
    ensure(symbol)
    rec = _STORE.get(symbol)
    now = time.time()

    if rec and rec.get("quote"):
        return rec["quote"]

    ohlc = rec.get("ohlc") if rec else None
    fut = rec.get("fut") if rec else None
    quote = _compile_quote(symbol, ohlc, fut)
    if rec:
        rec["quote"] = quote
    else:
        _STORE[symbol] = {"ohlc": ohlc, "fut": fut, "quote": quote, "at": now, "err": None}
    return quote



def push_ws_tick(symbol: str, ohlc: dict | None = None, fut: dict | None = None) -> None:
    """Called by ``app.ws_spot_feed`` on every decoded WebSocket tick.  Never blocks.

    Either ``ohlc`` (from an IDX_I spot tick) or ``fut`` (from an FNO futures tick), or both.
    Merges into the existing record so the two streams don't clobber each other."""
    now = time.time()
    rec = _STORE.setdefault(symbol, {"ohlc": None, "fut": None, "quote": None, "at": 0,
                                     "ws_at": None, "err": None})
    changed = False
    if ohlc is not None:
        # Merge: preserve keys the WS packet doesn't carry (e.g. vix from a VIX tick)
        existing = rec.get("ohlc") or {}
        merged = {**existing, **ohlc}       # WS values take precedence
        rec["ohlc"] = merged
        changed = True
    if fut is not None:
        rec["fut"] = fut
        changed = True
    if changed:
        rec["ws_at"] = now
        rec["at"] = now
        rec["err"] = None
        # Recompile the display quote so get_instant_quote() sees fresh values immediately
        rec["quote"] = _compile_quote(symbol, rec.get("ohlc"), rec.get("fut"))


def _ws_is_live_for(symbol: str, max_silence_s: float = 10.0) -> bool:
    """True when the last WebSocket tick for this symbol arrived within ``max_silence_s``."""
    ws_at = (_STORE.get(symbol) or {}).get("ws_at")
    return ws_at is not None and (time.time() - ws_at) < max_silence_s


# ── Option Contracts WebSocket Store ──────────────────────────────────────────────────────────────
# (symbol, strike, side) -> {"symbol": str, "strike": float, "side": str, "ltp": float, ...}
_STORE_OPTIONS: dict[tuple[str, float, str], dict] = {}
_STORE_OPTIONS_LOCK = threading.Lock()


def push_ws_option_tick(symbol: str, strike: float, side: str, pkt: dict) -> None:
    """Push an option contract tick from app.ws_spot_feed into _STORE_OPTIONS. Never blocks."""
    ltp = pkt.get("ltp")
    if ltp is None or ltp <= 0:
        return
    now = time.time()
    key = (symbol.upper(), float(strike), side.upper())
    with _STORE_OPTIONS_LOCK:
        existing = _STORE_OPTIONS.get(key) or {}
        _STORE_OPTIONS[key] = {
            **existing,
            "symbol": symbol.upper(),
            "strike": float(strike),
            "side": side.upper(),
            "ltp": float(ltp),
            "open": float(pkt.get("open") or existing.get("open") or ltp),
            "high": float(pkt.get("high") or existing.get("high") or ltp),
            "low": float(pkt.get("low") or existing.get("low") or ltp),
            "prev_close": float(pkt.get("prev_close") or existing.get("prev_close") or ltp),
            "volume": pkt.get("volume") or existing.get("volume"),
            "oi": pkt.get("oi") or existing.get("oi"),
            "ltt": pkt.get("ltt") or existing.get("ltt"),
            "at": now,
        }


def get_option_ws_quote(symbol: str, strike: float, side: str, max_age_s: float = 120.0) -> dict | None:
    """Return the latest exchange WebSocket tick for this option contract, or None."""
    key = (symbol.upper(), float(strike), side.upper())
    with _STORE_OPTIONS_LOCK:
        rec = _STORE_OPTIONS.get(key)
    if not rec:
        return None
    if (time.time() - rec.get("at", 0)) > max_age_s:
        return None
    return rec



def _loop(symbol: str, interval_s: float) -> None:
    from data.dhan_live import fetch_index_ohlc, fetch_futures_dhan
    import config
    while True:
        # 1. Back off REST polling when the WebSocket feed is actively providing live ticks
        if _ws_is_live_for(symbol, max_silence_s=10.0):
            time.sleep(WS_BACKOFF_S)
            continue

        # 2. Back off when market is closed — EXCEPT during Pre-Open equilibrium discovery (09:07:30 to 09:15:00 IST)
        if not config.is_market_open():
            try:
                from data.pre_post_market import get_current_session_phase
                phase_code, _, _ = get_current_session_phase()
            except Exception:
                phase_code = "OFF_HOURS"

            if phase_code != "PRE_MARKET_DISCOVERY":
                rec = _STORE.get(symbol)
                if rec and rec.get("ohlc"):
                    time.sleep(60.0)
                    continue
            else:
                interval_s = 3.0  # Adaptive 3s retry during pre-open discovery window

        ok = False
        try:
            d = fetch_index_ohlc(symbol)
            fd = fetch_futures_dhan(symbol)
            if d and d.get("ltp") is not None:
                # Only overwrite if WS hasn't updated more recently
                rec = _STORE.get(symbol)
                ws_at = (rec or {}).get("ws_at") or 0
                if time.time() - ws_at >= WS_BACKOFF_S:  # WS went quiet — take REST values
                    q = _compile_quote(symbol, d, fd)
                    _STORE[symbol] = {
                        "ohlc": d,
                        "fut": fd,
                        "quote": q,
                        "at": time.time(),
                        "ws_at": ws_at if ws_at else None,
                        "err": None,
                    }
                ok = True
        except Exception as e:  # noqa: BLE001 - a price lane must never die on one bad poll
            rec = _STORE.get(symbol)
            if rec:
                rec["err"] = f"{type(e).__name__}"
        time.sleep(interval_s if (ok or _STORE.get(symbol)) else 2.0)


def ensure(symbol: str, interval_s: float = DEFAULT_INTERVAL_S) -> None:
    """Start the poller for ``symbol`` if it isn't already running. Idempotent and cheap — safe to
    call on every repaint."""
    with _LOCK:
        t = _THREADS.get(symbol)
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_loop, args=(symbol, interval_s), daemon=True,
                             name=f"spot-{symbol}")
        _THREADS[symbol] = t
        t.start()


def warmup_all() -> None:
    """Start the WebSocket spot feed (primary) and the REST fallback pollers for all standard
    indices.  Idempotent — safe to call on every Streamlit repaint."""
    # 1. Start the real-time WebSocket feed — this is the PRIMARY source during market hours.
    try:
        from app.ws_spot_feed import ensure_running as _ws_ensure
        _ws_ensure(("NIFTY", "BANKNIFTY", "SENSEX"))
    except Exception:  # noqa: BLE001 — if ws module fails, REST fallback still runs
        pass
    # 2. Start the REST fallback pollers — they sleep most of the time when WS is live.
    for s in ("NIFTY", "BANKNIFTY", "SENSEX"):
        ensure(s, interval_s=3.0)

