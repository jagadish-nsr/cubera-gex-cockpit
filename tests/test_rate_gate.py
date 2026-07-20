"""The shared Dhan rate gate — several independent lanes must not 429 each other.

Lanes hitting Dhan concurrently: the window chain fetcher, the 10s gamma-blast lane, the 10s spot
lane, futures capture, the sector fallback. Before this, /optionchain had a global throttle but no
priority (so the fetcher's 4 back-to-back calls starved the blast lane's 10s beat to ~20s) and
/marketfeed had NO gate at all across 5 call sites.
"""

import threading
import time

import pytest

import data.dhan_live as dl


@pytest.fixture(autouse=True)
def _restore_gate():
    """These tests rewrite the module-global buckets — put the real limits back afterwards so a
    later test (or a live call) isn't left running on a 0.1s toy limit."""
    limits, buckets = dict(dl._LIMITS), dict(dl._BUCKETS)
    yield
    dl._LIMITS.clear(); dl._LIMITS.update(limits)
    dl._BUCKETS.clear(); dl._BUCKETS.update(buckets)


def _fresh(limits):
    dl._LIMITS.clear()
    dl._LIMITS.update(limits)
    dl._BUCKETS.clear()
    dl._BUCKETS.update({k: {"last": 0.0, "hi": 0, "cv": threading.Condition(), "calls": []}
                        for k in limits})


def test_gate_enforces_the_minimum_spacing():
    _fresh({"chain": 0.20})
    t0 = time.time()
    stamps = []
    for _ in range(4):
        dl._gate("chain")
        stamps.append(time.time() - t0)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 0.19 for g in gaps), gaps


def test_buckets_are_independent():
    """A chain burst must not delay a marketfeed call — separate quotas, separate budgets."""
    _fresh({"chain": 0.30, "quote": 0.01})
    dl._gate("chain")
    t = time.time()
    dl._gate("quote")
    assert (time.time() - t) < 0.05


def test_high_priority_takes_the_next_slot():
    """The window fetcher must not starve the blast lane. With low-priority callers queued, a
    high-priority caller goes first."""
    _fresh({"chain": 0.15})
    dl._gate("chain")                       # consume the current slot
    order = []
    lock = threading.Lock()

    def lo(i):
        dl._gate("chain", hi=False)
        with lock:
            order.append(f"lo{i}")

    def hi():
        time.sleep(0.02)                    # arrives AFTER the low-priority callers are waiting
        dl._gate("chain", hi=True)
        with lock:
            order.append("hi")

    ts = [threading.Thread(target=lo, args=(i,)) for i in range(3)] + [threading.Thread(target=hi)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=5)
    assert order[0] == "hi", f"priority caller did not win the next slot: {order}"


def test_gate_is_shared_across_threads():
    """Two components on different threads share ONE budget — that is the whole point."""
    _fresh({"chain": 0.10})
    stamps = []
    lock = threading.Lock()

    def worker():
        for _ in range(3):
            dl._gate("chain")
            with lock:
                stamps.append(time.time())

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 0.09 for g in gaps), gaps       # 6 calls, never closer than the limit


def test_marketfeed_is_gated_by_path():
    """/marketfeed used to bypass the throttle entirely — the bucket is now derived from the path
    so a new call site cannot forget it."""
    _fresh({"chain": 5.0, "quote": 0.01})
    sent = {}

    class _R:
        ok = True

    def _fake_post(url, json=None, headers=None, timeout=None):
        sent["url"] = url
        return _R()

    import requests
    orig = requests.post
    requests.post = _fake_post
    try:
        t = time.time()
        dl._post("/marketfeed/quote", {})           # must use the fast "quote" bucket, not "chain"
        assert (time.time() - t) < 0.5
        assert dl._BUCKETS["quote"]["calls"] and not dl._BUCKETS["chain"]["calls"]
    finally:
        requests.post = orig


def test_rate_report_makes_the_budget_visible():
    _fresh({"chain": 3.1, "quote": 1.1})
    for _ in range(3):
        dl._BUCKETS["chain"]["calls"].append(time.time())
    r = dl.rate_report(60.0)
    assert r["chain"]["calls"] == 3
    assert r["chain"]["limit_per_min"] > 3
    assert 0.0 <= r["chain"]["headroom"] <= 1.0
