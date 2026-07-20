"""Fast OPTION lane for the gamma blast — the NEAREST-EXPIRY chain, every ~10s.

The blast score reads writer covering (ΔOI on the pain side) and premium expansion, which used to
come from the full-window chain — so the *fuel* reading sat at that 1-5 min beat even after the
cross itself was timed to 10s.

The cost that forced this was the WINDOW, not the chain: measured 20-Jul-2026,

    4-expiry window chain   12.4s   1814 rows
    1-expiry chain           1.0s    448 rows   (3.1s when the /optionchain throttle bites)
    targeted 6-contract quote 0.47s     6 contracts

so one expiry at a 10s beat is cheap, and it returns the WHOLE ladder rather than a few
pre-chosen strikes: the wall-ahead read works at any strike, anchors can move without
reconfiguring a watch list, and there is one parsing path instead of two. (The targeted
``dhan_live.fetch_option_quotes`` remains for cases that genuinely need 2 contracts.)

Rate budget: a 10s poll is 6 /optionchain calls per minute against a 1-per-3s limit (~20/min),
alongside the main fetcher's 4 calls every 1-5 min.

Dhan's OI is share-denominated; harmless here because the score uses ΔOI/OI *ratios*, which are
scale-free (unlike ₹ turnover, where that assumption caused a 65× error).

Same discipline as the spot lane: daemon thread, publish to a process-global store, render only
reads. A tick older than ``STALE_AFTER_S`` is dropped rather than presented as live.
"""

from __future__ import annotations

import threading
import time

_STORE: dict = {}                  # symbol -> {"q": dict, "at": epoch}
_THREADS: dict = {}
_CFG: dict = {}                    # symbol -> {"band_pts": float|None, "interval": s}
_LOCK = threading.Lock()

DEFAULT_INTERVAL_S = 10.0
STALE_AFTER_S = 60.0               # the chain fallback is only minutes old, so don't cling to junk


def latest(symbol: str) -> dict | None:
    """Most recent ladder {strike: {call_oi, put_oi, call_ltp, put_ltp}} (+ spot, as_of) or None."""
    rec = _STORE.get(symbol)
    if not rec or not rec.get("q"):
        return None
    if (time.time() - rec["at"]) > STALE_AFTER_S:
        return None
    return rec["q"]


def _pull(symbol: str, band_pts):
    """One NEAREST-EXPIRY chain → {strike: {call_oi, put_oi, call_ltp, put_ltp}} (+ spot, as_of).

    Measured: a 1-expiry chain is **1.0s** (3.1s if the /optionchain throttle bites) for all 448
    strikes, versus 12.4s for the 4-expiry window and 0.47s for a targeted 6-contract quote. At a
    10s beat the chain is comfortably affordable and strictly more useful — the WHOLE ladder
    refreshes, so the wall-ahead read works at any strike, the anchors can move without
    reconfiguring a watch list, and there is one parsing path instead of two.
    """
    from data import dhan_live
    from data.nse import nearest_expiry_premiums, parse_nse_option_chain, per_strike_oi
    payload = dhan_live.fetch_chain(symbol, max_expiries=1)      # nearest expiry only
    rows, spot = parse_nse_option_chain(payload)
    strikes = per_strike_oi(payload, spot=spot, band_pts=band_pts)
    prem = nearest_expiry_premiums(rows)
    q: dict = {"spot": spot, "as_of": None}
    for s in strikes or []:
        k = float(s["strike"])
        p = prem.get(k, {})
        q[k] = {"call_oi": s.get("call_oi"), "put_oi": s.get("put_oi"),
                "call_ltp": p.get("call_ltp"), "put_ltp": p.get("put_ltp")}
    return q if len(q) > 2 else None


def _loop(symbol: str) -> None:
    while True:
        cfg = _CFG.get(symbol) or {}
        interval = float(cfg.get("interval", DEFAULT_INTERVAL_S))
        got = False
        try:
            q = _pull(symbol, cfg.get("band_pts"))
            if q:
                _STORE[symbol] = {"q": q, "at": time.time()}
                got = True
        except Exception:  # noqa: BLE001 - never die on one bad poll
            pass
        time.sleep(interval if (got or _STORE.get(symbol)) else 3.5)


def ensure(symbol: str, band_pts=None, interval_s: float = DEFAULT_INTERVAL_S) -> None:
    """Run the nearest-expiry lane for ``symbol`` (idempotent — refreshes config each call, starts
    the daemon only if it isn't alive). Safe to call on every repaint. ``band_pts`` just bounds the
    per-strike slice around spot, as elsewhere."""
    with _LOCK:
        _CFG[symbol] = {"band_pts": band_pts, "interval": float(interval_s)}
        t = _THREADS.get(symbol)
        if t is None or not t.is_alive():
            t = threading.Thread(target=_loop, args=(symbol,), daemon=True, name=f"blast-{symbol}")
            _THREADS[symbol] = t
            t.start()
