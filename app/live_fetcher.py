"""Background live-snapshot fetcher — keeps the (slow, ~12s multi-expiry) Dhan pull OFF the
Streamlit render thread so the dashboard NEVER freezes/dims while a snapshot is assembling.

A daemon thread per symbol fetches on the wall-clock grid (pre-fetched by ``lead`` seconds so the
snapshot is ready AT the boundary) and publishes to a process-global store. The UI fragment just
reads the latest published snapshot — a pure in-memory read, so repaints are instant and there is
no network in the render path. The thread touches NO Streamlit API (only ``load_dashboard``, pure
Python + network), so it needs no script-run context and is safe to run detached.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

_STORE: dict = {}       # symbol -> {"data", "as_of", "fetched", "err"}
_THREADS: dict = {}     # symbol -> Thread
_CFG: dict = {}         # symbol -> {"use_demo", "live_source", "interval", "lead"}
_FORCE: dict = {}       # symbol -> Event, set to ask for an off-grid pull now
_LOCK = threading.Lock()


def latest(symbol: str):
    """The most recently published snapshot dict for ``symbol`` (or None if none yet)."""
    return _STORE.get(symbol)


def _event(symbol: str) -> threading.Event:
    with _LOCK:
        ev = _FORCE.get(symbol)
        if ev is None:
            ev = _FORCE[symbol] = threading.Event()
        return ev


def force(symbol: str) -> None:
    """Ask the background thread to pull NOW instead of waiting for the next grid boundary.
    Returns immediately — the manual Refresh button uses this so it never blocks the render."""
    _event(symbol).set()


def _loop(symbol: str) -> None:
    from app.data_access import load_dashboard
    served = -1.0
    while True:
        cfg = _CFG.get(symbol) or {}
        interval = max(30, int(cfg.get("interval", 60)))
        lead = float(cfg.get("lead", 15.0))
        now = time.time()
        if served < 0:                                   # first fetch: immediately, stamp to grid
            stamp, wait = (now // interval) * interval, 0.0
        else:                                            # then align to the next boundary, pre-fetched
            nb = (int(now // interval) + 1) * interval
            if nb <= served:
                nb += interval
            stamp, wait = nb, max(0.0, nb - lead - now)
        ev = _event(symbol)
        if wait:
            if ev.wait(wait):           # a manual Refresh landed → pull now, off the grid
                stamp = time.time()
        ev.clear()
        try:
            data = load_dashboard(None, bool(cfg.get("use_demo")), symbol, cfg.get("live_source"))
            as_of = datetime.fromtimestamp(stamp, tz=timezone.utc) + timedelta(hours=5, minutes=30)
            _STORE[symbol] = {"data": data, "as_of": as_of.replace(tzinfo=None),
                              "fetched": datetime.now(timezone.utc), "err": None}
        except Exception as e:  # noqa: BLE001 - keep the loop alive; surface the error to the UI
            prev = dict(_STORE.get(symbol) or {})
            prev["err"] = str(e)
            _STORE[symbol] = prev
        served = stamp


def ensure(symbol: str, use_demo: bool, live_source, interval_s: int, lead_s: float = 15.0) -> None:
    """Ensure a background fetcher is running for ``symbol`` with the current settings (idempotent —
    updates the live config each call; (re)starts the daemon thread only if it isn't alive)."""
    with _LOCK:
        _CFG[symbol] = {"use_demo": use_demo, "live_source": live_source,
                        "interval": int(interval_s), "lead": float(lead_s)}
        t = _THREADS.get(symbol)
        if t is None or not t.is_alive():
            t = threading.Thread(target=_loop, args=(symbol,), name=f"live-{symbol}", daemon=True)
            _THREADS[symbol] = t
            t.start()
