"""Background live-snapshot fetcher — one ingest loop per (symbol, window).

Keeps the slow (~12s multi-expiry) Dhan pull OFF every UI / HTTP / WebSocket
thread. Consumers only call ``latest()`` (in-memory read) or ``force()``
(non-blocking wake). The loop is the only place ``load_dashboard(use_demo=False)``
runs during market hours.

A daemon thread per key fetches on the wall-clock grid (pre-fetched by ``lead``
seconds so the snapshot is ready AT the boundary) and publishes to a
process-global store. Successful pulls are also written to
``data/journal/latest_snap_{SYMBOL}.pkl`` and LKGS (via ``load_dashboard``).
"""

from __future__ import annotations

import os
import pickle
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_INTERVAL_S = 60
DEFAULT_LEAD_S = 15.0
_SNAP_DIR = Path("data") / "journal"

_STORE: dict = {}       # key -> {"data", "as_of", "fetched", "err", "window"}
_THREADS: dict = {}     # key -> Thread
_CFG: dict = {}         # key -> config dict
_FORCE: dict = {}       # key -> Event
_LOCK = threading.Lock()


def _key(symbol: str, window: str | None = None):
    """Store key. Bare symbol keeps Streamlit ``latest("NIFTY")`` working."""
    sym = (symbol or "").upper().strip()
    if window:
        return (sym, str(window))
    return sym


def latest(symbol: str, window: str | None = None):
    """The most recently published snapshot dict for ``symbol`` (or None). Never blocks."""
    return _STORE.get(_key(symbol, window))


def _event(key) -> threading.Event:
    with _LOCK:
        ev = _FORCE.get(key)
        if ev is None:
            ev = _FORCE[key] = threading.Event()
        return ev


def force(symbol: str, window: str | None = None) -> None:
    """Ask the background thread to pull NOW. Returns immediately — no fetch here."""
    if window:
        _event(_key(symbol, window)).set()
        return
    keys = [_key(symbol)]
    with _LOCK:
        keys.extend(
            k for k in _FORCE
            if isinstance(k, tuple) and k[0] == (symbol or "").upper().strip()
        )
    for k in keys:
        _event(k).set()


def publish_snapshot(symbol: str, window: str | None, data):
    """Store a computed dashboard and write the off-market pickle.

    Returns the snapshot's fetched time (UTC). Refuses a synthetic book.
    An explicit off-market Dhan sync uses this so the UI reads that book.
    """
    if data is None:
        raise ValueError("no dashboard")
    src = str(getattr(data, "source", "") or "").lower()
    if any(k in src for k in ("demo", "synthetic", "mock", "test")):
        raise ValueError("refusing to publish a synthetic book")
    fetched = getattr(data, "fetched_at", None) or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    sym = (symbol or "").upper().strip()
    store_key = _key(sym, window)
    _STORE[store_key] = {
        "data": data,
        "as_of": fetched.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None),
        "fetched": fetched,
        "err": None,
        "window": window,
    }
    _persist_pickle(sym, window, data)
    return fetched


def _persist_pickle(symbol: str, window: str | None, data) -> None:
    """Atomic EOD/live snapshot so off-market FastAPI can read without Dhan."""
    if data is None:
        return
    src = str(getattr(data, "source", "") or "").lower()
    if any(k in src for k in ("demo", "synthetic", "mock", "test")):
        return
    try:
        _SNAP_DIR.mkdir(parents=True, exist_ok=True)
        snap_path = _SNAP_DIR / f"latest_snap_{symbol.upper()}.pkl"
        tmp_path = str(snap_path) + ".tmp"
        payload = {
            "data": data,
            "symbol": symbol.upper(),
            "window": window,
            "fetched_at": datetime.now(timezone.utc),
        }
        with open(tmp_path, "wb") as f:
            pickle.dump(payload, f)
        os.replace(tmp_path, snap_path)
    except Exception:
        pass


def _loop(store_key) -> None:
    from app.data_access import load_dashboard
    served = -1.0
    while True:
        cfg = _CFG.get(store_key) or {}
        use_demo = bool(cfg.get("use_demo"))
        symbol = cfg.get("symbol") or (
            store_key[0] if isinstance(store_key, tuple) else str(store_key)
        )
        window = cfg.get("window")
        import config
        # This loop stays idle off-market. Dhan is called only by the explicit
        # sync: manual, 06:30 IST, or a new session token.
        if not config.is_market_open() and not use_demo:
            time.sleep(30.0)
            continue

        interval = max(30, int(cfg.get("interval", DEFAULT_INTERVAL_S)))
        lead = float(cfg.get("lead", DEFAULT_LEAD_S))
        now = time.time()
        if served < 0:
            stamp, wait = (now // interval) * interval, 0.0
        else:
            nb = (int(now // interval) + 1) * interval
            if nb <= served:
                nb += interval
            stamp, wait = nb, max(0.0, nb - lead - now)
        ev = _event(store_key)
        if wait:
            if ev.wait(wait):
                stamp = time.time()
        ev.clear()
        try:
            data = load_dashboard(
                window=window,
                use_demo=use_demo,
                symbol=symbol,
                live_source=cfg.get("live_source"),
                sign_model=cfg.get("sign_model"),
            )
            as_of = datetime.fromtimestamp(stamp, tz=timezone.utc) + timedelta(hours=5, minutes=30)
            _STORE[store_key] = {
                "data": data,
                "as_of": as_of.replace(tzinfo=None),
                "fetched": datetime.now(timezone.utc),
                "err": None,
                "window": window,
            }
            _persist_pickle(symbol, window, data)
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            prev = dict(_STORE.get(store_key) or {})
            prev["err"] = str(e)
            _STORE[store_key] = prev
        served = stamp


def ensure(
    symbol: str,
    use_demo: bool,
    live_source,
    interval_s: int,
    lead_s: float = DEFAULT_LEAD_S,
    sign_model=None,
    window: str | None = None,
) -> None:
    """Ensure a background fetcher is running for this symbol/window. Idempotent."""
    store_key = _key(symbol, window)
    with _LOCK:
        _CFG[store_key] = {
            "symbol": (symbol or "").upper().strip(),
            "window": window,
            "use_demo": use_demo,
            "live_source": live_source,
            "interval": int(interval_s),
            "lead": float(lead_s),
            "sign_model": sign_model,
        }
        t = _THREADS.get(store_key)
        if t is None or not t.is_alive():
            name = f"live-{symbol}" if not window else f"live-{symbol}-{window}"
            t = threading.Thread(target=_loop, args=(store_key,), name=name, daemon=True)
            _THREADS[store_key] = t
            t.start()
