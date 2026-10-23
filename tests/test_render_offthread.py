"""The render thread must never do network I/O — that is what shaded the page every repaint.

`capture_futures_snapshot` fetches FIRST and applies the min-gap AFTER, so calling it inline cost a
live Dhan round-trip (measured 7.1s) on every fragment poll and then usually discarded the result.
"""

import time
from datetime import datetime, timezone

import app.simple_dashboard as sd


def test_futures_capture_is_gated_before_any_network_call(monkeypatch):
    started = []
    monkeypatch.setattr(sd, "_FUT_CAP", {})

    class _T:
        def __init__(self, *a, **k):
            started.append(k.get("name"))

        def start(self):
            pass

    monkeypatch.setattr("threading.Thread", _T)
    now = datetime.now(timezone.utc)
    sd._capture_futures_bg("NIFTY", "dhan", now)
    assert len(started) == 1                     # first call is due → one background thread
    for _ in range(20):                          # every subsequent repaint inside the gap: nothing
        sd._capture_futures_bg("NIFTY", "dhan", now)
    assert len(started) == 1, "repaints must not re-fetch inside the 3-min gap"


def test_capture_slot_is_claimed_before_the_thread_runs(monkeypatch):
    """Overlapping repaints must not double-fire while the first capture is still in flight."""
    monkeypatch.setattr(sd, "_FUT_CAP", {})
    calls = []
    monkeypatch.setattr("threading.Thread",
                        lambda *a, **k: type("T", (), {"start": lambda s: calls.append(1)})())
    now = datetime.now(timezone.utc)
    sd._capture_futures_bg("NIFTY", "dhan", now)
    sd._capture_futures_bg("NIFTY", "dhan", now)
    assert len(calls) == 1
    assert sd._FUT_CAP["NIFTY"] > 0              # slot claimed up-front, not after completion


def test_day_file_reads_are_cached_between_repaints():
    reads = []

    def _load():
        reads.append(1)
        return "frame"

    for _ in range(10):
        assert sd._cached_day("t", ("k",), _load, ttl=30.0) == "frame"
    assert len(reads) == 1, "the same day file must not be re-parsed on every repaint"


def test_cached_day_expires():
    reads = []
    sd._DAY_CACHE.pop(("t2", "k"), None)
    for _ in range(2):
        sd._cached_day("t2", ("k",), lambda: reads.append(1), ttl=0.0)
    assert len(reads) == 2


def test_spot_ticker_read_is_instant_and_never_blocks():
    from app import spot_ticker
    spot_ticker._STORE.clear()
    t = time.time()
    assert spot_ticker.latest("NIFTY") is None       # nothing published yet → no wait, no network
    assert (time.time() - t) < 0.05


def test_spot_ticker_latest_quote_is_instant():
    from app import spot_ticker
    spot_ticker._STORE.clear()
    t = time.time()
    assert spot_ticker.latest_quote("NIFTY") is None
    assert (time.time() - t) < 0.05
    spot_ticker._STORE["NIFTY"] = {
        "ohlc": {"ltp": 22600.0},
        "quote": {"spot": 22600.0, "fut_px": 22620.0},
        "at": time.time(),
        "err": None,
    }
    q = spot_ticker.latest_quote("NIFTY")
    assert q["spot"] == 22600.0
    spot_ticker._STORE.clear()


def test_spot_ticker_drops_a_stale_tick():
    from app import spot_ticker
    spot_ticker._STORE["NIFTY"] = {"ohlc": {"ltp": 1.0}, "at": time.time() - 999, "err": None}
    assert spot_ticker.latest("NIFTY") is None       # too old to present as live
    spot_ticker._STORE["NIFTY"] = {"ohlc": {"ltp": 1.0}, "at": time.time(), "err": None}
    assert spot_ticker.latest("NIFTY")["ltp"] == 1.0
    spot_ticker._STORE.clear()


# ── the Refresh button must not block the render thread ────────────────────────────────────────
def test_force_is_non_blocking_and_wakes_the_fetcher():
    from app import live_fetcher as lf
    lf._FORCE.clear()
    t = time.time()
    lf.force("NIFTY")
    assert (time.time() - t) < 0.05          # returns immediately — no fetch on this thread
    assert lf._event("NIFTY").is_set()       # the background loop will wake and pull off-grid
    lf._event("NIFTY").clear()


def test_event_is_stable_per_symbol():
    from app import live_fetcher as lf
    lf._FORCE.clear()
    assert lf._event("NIFTY") is lf._event("NIFTY")
    assert lf._event("NIFTY") is not lf._event("SENSEX")


def test_live_fetcher_window_keys_are_independent():
    from app import live_fetcher as lf
    lf._STORE.clear()
    lf._STORE["NIFTY"] = {"data": "default"}
    lf._STORE[("NIFTY", "current_week")] = {"data": "week"}
    assert lf.latest("NIFTY")["data"] == "default"
    assert lf.latest("NIFTY", "current_week")["data"] == "week"
    assert lf.latest("NIFTY", "monthly") is None


def test_force_with_window_does_not_wake_default_key():
    from app import live_fetcher as lf
    lf._FORCE.clear()
    lf.force("NIFTY", window="current_week")
    assert lf._event(("NIFTY", "current_week")).is_set()
    assert not lf._event("NIFTY").is_set()
    lf._event(("NIFTY", "current_week")).clear()
