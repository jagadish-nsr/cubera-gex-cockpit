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


def latest_quote(symbol: str) -> dict | None:
    """Compiled spot/futures/VIX quote, or None. Memory read only — never blocks."""
    rec = _STORE.get(symbol)
    if not rec:
        return None
    if (time.time() - rec.get("at", 0)) > STALE_AFTER_S:
        return None
    return rec.get("quote")


def _compile_quote(symbol: str, ohlc: dict | None, fut: dict | None) -> dict:
    """Compile a complete instant market quote dictionary without blocking."""
    snap = None
    if not ohlc or not ohlc.get("ltp"):
        try:
            import config
            if config.is_market_open():
                from data.market_snapshot import get_snapshot
                snap = get_snapshot(symbol)
            else:
                from data.market_snapshot import resolve_offmarket_snapshot
                snap = resolve_offmarket_snapshot(symbol)
        except Exception:
            pass

    snap_spot = float(snap.get("spot", 0.0)) if snap else 0.0
    spot = float(ohlc.get("ltp")) if (ohlc and ohlc.get("ltp")) else snap_spot
    prev_close = float(ohlc.get("prev_close")) if (ohlc and ohlc.get("prev_close")) else float(snap.get("prev_close", spot) if snap else spot)
    open_px = float(ohlc.get("open")) if (ohlc and ohlc.get("open")) else float(snap.get("open", spot) if snap else spot)
    high_px = float(ohlc.get("high")) if (ohlc and ohlc.get("high")) else float(snap.get("high", spot) if snap else spot)
    low_px = float(ohlc.get("low")) if (ohlc and ohlc.get("low")) else float(snap.get("low", spot) if snap else spot)
    as_of = (ohlc.get("as_of") if ohlc else None) or (snap.get("as_of") if snap else None)

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

    # Near futures and basis. A missing futures print stays missing.
    fut_px = float(fut.get("price")) if (fut and fut.get("price")) else None
    if fut_px is not None and fut_px <= 0:
        fut_px = None
    basis = (fut_px - spot) if (fut_px is not None and spot > 0) else None
    basis_color = "var(--go)" if (basis is not None and basis >= 0) else ("var(--stop)" if basis is not None else "var(--ink-mute)")
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
    import config
    market_open = config.is_market_open()
    snap = None
    if not market_open:
        from data.market_snapshot import resolve_offmarket_snapshot
        snap = resolve_offmarket_snapshot(symbol)

    stored = (rec or {}).get("quote")
    if stored and (market_open or snap is None or stored.get("as_of") == snap.get("as_of")):
        return stored

    ohlc = None if (not market_open and snap) else (rec.get("ohlc") if rec else None)
    if ohlc is None:
        if not market_open:
            if snap:
                ohlc = {
                    "ltp": snap.get("spot"),
                    "close": snap.get("spot"),
                    "prev_close": snap.get("prev_close"),
                    "open": snap.get("open"),
                    "high": snap.get("high"),
                    "low": snap.get("low"),
                    "vix": {"last": snap.get("vix")} if snap.get("vix") is not None else {},
                    "as_of": snap.get("as_of"),
                }
        else:
            try:
                from data.dhan_live import fetch_index_ohlc
                ohlc = fetch_index_ohlc(symbol)
            except Exception:
                pass
    fut = rec.get("fut") if rec else None
    quote = _compile_quote(symbol, ohlc, fut)
    if rec:
        rec["quote"] = quote
        if ohlc:
            rec["ohlc"] = ohlc
    else:
        _STORE[symbol] = {"ohlc": ohlc, "fut": fut, "quote": quote, "at": now, "err": None}
    return quote



_LAST_SNAPSHOT_SAVE: dict[str, float] = {}
_SNAPSHOT_SAVE_INTERVAL_S = 15.0


def _bg_save_snapshot(symbol: str, q: dict) -> None:
    try:
        from data.market_snapshot import save_snapshot
        save_snapshot(
            symbol,
            spot=q.get("spot"),
            prev_close=q.get("prev_close"),
            open_px=q.get("open"),
            high=q.get("high"),
            low=q.get("low"),
            vix=q.get("vix"),
            as_of=q.get("as_of"),
            source="ws-tick",
        )
    except Exception:
        pass


def note_synced_book(symbol: str, ohlc: dict | None) -> None:
    """Replace the stored quote with a book just computed from a Dhan sync.

    A live WebSocket print is left in place. Off-market, the REST poller
    would otherwise keep an older quote on top of the new chain.
    """
    sym = (symbol or "").upper().strip()
    if not sym or not ohlc or not ohlc.get("ltp"):
        return
    if _ws_is_live_for(sym):
        return
    rec = _STORE.setdefault(sym, {"ohlc": None, "fut": None, "quote": None, "at": 0, "ws_at": None, "err": None})
    rec["ohlc"] = dict(ohlc)
    rec["quote"] = _compile_quote(sym, rec["ohlc"], rec.get("fut"))
    rec["at"] = time.time()
    rec["err"] = None


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
        rec["quote"] = _compile_quote(symbol, rec.get("ohlc"), rec.get("fut"))

        # Throttle SQLite snapshot persistence: at most once per 15s in background
        import config
        if config.is_market_open() and now - _LAST_SNAPSHOT_SAVE.get(symbol, 0.0) >= _SNAPSHOT_SAVE_INTERVAL_S:
            _LAST_SNAPSHOT_SAVE[symbol] = now
            q = rec["quote"]
            threading.Thread(target=_bg_save_snapshot, args=(symbol, q), daemon=True, name=f"snap-{symbol}").start()


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
        # Off-market hours guard: never call Dhan API when market is closed
        if not config.is_market_open():
            try:
                from data.pre_post_market import get_current_session_phase
                phase_code, _, _ = get_current_session_phase()
            except Exception:
                phase_code = "OFF_HOURS"

            # Hydrate from the last completed session, and replace a stored
            # quote when a newer session snapshot appears.
            from data.market_snapshot import resolve_offmarket_snapshot
            snap = resolve_offmarket_snapshot(symbol)
            stored_asof = ((_STORE.get(symbol) or {}).get("ohlc") or {}).get("as_of")
            if snap and stored_asof != snap.get("as_of"):
                d_init = {
                    "ltp": snap.get("spot"),
                    "close": snap.get("spot"),
                    "prev_close": snap.get("prev_close"),
                    "open": snap.get("open"),
                    "high": snap.get("high"),
                    "low": snap.get("low"),
                    "vix": {"last": snap.get("vix")} if snap.get("vix") is not None else {},
                    "as_of": snap.get("as_of"),
                }
                prev_fut = (_STORE.get(symbol) or {}).get("fut")
                q_init = _compile_quote(symbol, d_init, prev_fut)
                _STORE[symbol] = {
                    "ohlc": d_init,
                    "fut": None,
                    "quote": q_init,
                    "at": time.time(),
                    "ws_at": None,
                    "err": None,
                }

            if phase_code != "PRE_MARKET_DISCOVERY":
                time.sleep(30.0)
                continue
            else:
                interval_s = 3.0  # Adaptive retry during pre-open discovery window

        ok = False
        try:
            d = fetch_index_ohlc(symbol)
            fd = fetch_futures_dhan(symbol)
            if d and d.get("ltp") is not None:
                # Only overwrite if WS hasn't updated more recently
                rec = _STORE.get(symbol)
                ws_at = (rec or {}).get("ws_at") or 0
                if time.time() - ws_at >= 10.0:  # WS went quiet — take REST values immediately
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

