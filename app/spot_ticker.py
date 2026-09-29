"""Fast SPOT lane — a ~10s background poll of the index quote, independent of the option chain.

The chain pull is expensive (~12-15s: one throttled /optionchain call per expiry) so the candle
could only move at that cadence. The index quote is a single cheap call (~450-500ms) on a different
endpoint, so the day candle — open/high/low/last — can update far faster without touching the
chain's budget or its 3s-per-call throttle.

Runs on a daemon thread and publishes into a process-global store; the render only ever reads that
store, so the page never waits on the network (the futures capture doing exactly that on the render
thread is what used to shade the page every repaint).

Deliberately NOT used to recompute levels: walls/flip/magnet stay as of the last chain pull (the
header timestamp says when). This lane moves the *price*, not the map — mixing a fresh spot into
stale positioning would be a silent inconsistency.

⚠ Dhan rate-limits /marketfeed by quota (measured: 429s appear intermittently even at ~1-2s
spacing). 10s is far inside that; a 429 simply leaves the previous good tick in place — a stale
tick is never re-stamped as fresh.
"""

from __future__ import annotations

import threading
import time

_STORE: dict = {}                  # symbol -> {"ohlc": dict, "at": epoch, "err": str|None}
_THREADS: dict = {}
_LOCK = threading.Lock()

DEFAULT_INTERVAL_S = 10.0
STALE_AFTER_S = 45.0               # older than this and we stop trusting the tick


def latest(symbol: str) -> dict | None:
    """The most recent published tick for ``symbol``, or None. Never blocks."""
    rec = _STORE.get(symbol)
    if not rec or not rec.get("ohlc"):
        return None
    if (time.time() - rec["at"]) > STALE_AFTER_S:
        return None                # gone quiet (429 storm / network) → let the caller fall back
    return rec["ohlc"]


def _loop(symbol: str, interval_s: float) -> None:
    from data.dhan_live import fetch_index_ohlc
    while True:
        ok = False
        try:
            d = fetch_index_ohlc(symbol)
            if d and d.get("ltp") is not None:
                _STORE[symbol] = {"ohlc": d, "at": time.time(), "err": None}
                ok = True
        except Exception as e:  # noqa: BLE001 - a price lane must never die on one bad poll
            rec = _STORE.get(symbol)
            if rec:
                rec["err"] = f"{type(e).__name__}"
        # Until the first tick lands, retry quickly (a cold 429 shouldn't cost a full interval);
        # once we have a price, settle into the steady beat.
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
