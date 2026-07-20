"""Fast OPTION lane for the gamma blast — OI + premium for the 2-3 WATCHED strikes, every ~10s.

The blast score reads writer covering (ΔOI on the pain side) and premium expansion. Those came from
the option chain, so the *fuel* reading sat at the chain's 1-5 min beat even after the cross itself
was timed to 10s. But the blast only ever watches the anchored ATM strike and its neighbours — and
Dhan's ``/marketfeed/quote`` returns ``oi`` and ``last_price`` for specific option contracts, so
2-3 strikes (4-6 contracts) come back in ONE ~0.5s call instead of a ~12s full-chain pull.

Verified live (20-Jul-2026): NSE_FNO quote for two option ids →
``{oi: 7129460, last_price: 104.2, volume: …, last_trade_time: …}``. OI is in SHARES like the rest
of the Dhan feed, which is harmless here: the score uses ΔOI/OI *ratios*, which are scale-free.

Same discipline as the spot lane: daemon thread, publish to a process-global store, render only
reads. A tick older than ``STALE_AFTER_S`` is dropped rather than presented as live, and a 429
leaves the previous tick in place.
"""

from __future__ import annotations

import threading
import time

_STORE: dict = {}                  # symbol -> {"q": dict, "at": epoch}
_THREADS: dict = {}
_CFG: dict = {}                    # symbol -> {"strikes": [...], "expiry": str|None, "interval": s}
_LOCK = threading.Lock()

DEFAULT_INTERVAL_S = 10.0
STALE_AFTER_S = 60.0               # the chain fallback is only minutes old, so don't cling to junk


def latest(symbol: str) -> dict | None:
    """Most recent quote bundle {strike: {call_oi, put_oi, call_ltp, put_ltp}, as_of} or None."""
    rec = _STORE.get(symbol)
    if not rec or not rec.get("q"):
        return None
    if (time.time() - rec["at"]) > STALE_AFTER_S:
        return None
    return rec["q"]


def _loop(symbol: str) -> None:
    from data.dhan_live import fetch_option_quotes
    while True:
        cfg = _CFG.get(symbol) or {}
        strikes = list(cfg.get("strikes") or [])
        interval = float(cfg.get("interval", DEFAULT_INTERVAL_S))
        got = False
        if strikes:
            try:
                q = fetch_option_quotes(symbol, strikes, cfg.get("expiry"))
                if q:
                    _STORE[symbol] = {"q": q, "at": time.time()}
                    got = True
            except Exception:  # noqa: BLE001 - never die on one bad poll
                pass
        time.sleep(interval if (got or _STORE.get(symbol)) else 2.0)


def ensure(symbol: str, strikes, expiry_iso: str | None = None,
           interval_s: float = DEFAULT_INTERVAL_S) -> None:
    """Track ``strikes`` for ``symbol`` (idempotent — updates the watch list each call, starts the
    daemon only if it isn't alive). Safe to call on every repaint."""
    with _LOCK:
        _CFG[symbol] = {"strikes": [float(k) for k in strikes if k is not None],
                        "expiry": expiry_iso, "interval": float(interval_s)}
        t = _THREADS.get(symbol)
        if t is None or not t.is_alive():
            t = threading.Thread(target=_loop, args=(symbol,), daemon=True, name=f"blast-{symbol}")
            _THREADS[symbol] = t
            t.start()
