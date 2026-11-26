"""Chain-fetch timing diagnostics: gate wait / HTTP split and the chain.timing event.

Diagnostics only. These tests pin two things: the accounting is right, and the fetch
result is unchanged.
"""
from __future__ import annotations

import types

import pytest


class _Resp:
    def __init__(self, status: int):
        self.status_code = status
        self.headers = {}


def test_post_records_gate_http_and_429_when_a_timing_window_is_open(monkeypatch):
    from data import dhan_live

    calls = []
    statuses = iter([200, 429])

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        return _Resp(next(statuses))

    monkeypatch.setattr(dhan_live, "_gate", lambda bucket="chain", hi=False: None)
    monkeypatch.setattr(dhan_live, "_headers", lambda: {})
    monkeypatch.setattr(dhan_live, "_note_rate_limit_429", lambda bucket, retry_after=None: None)
    import requests

    monkeypatch.setattr(requests, "post", fake_post)

    dhan_live.timing_begin()
    dhan_live._post("/optionchain", {"x": 1})
    dhan_live._post("/optionchain", {"x": 2})
    acc = dhan_live.timing_take()

    assert len(calls) == 2
    assert acc["calls"] == 2
    assert acc["throttled_429"] == 1
    assert acc["gate_ms"] >= 0.0 and acc["http_ms"] >= 0.0


def test_post_outside_a_window_leaves_no_state_and_timing_take_clears(monkeypatch):
    from data import dhan_live
    import requests

    monkeypatch.setattr(dhan_live, "_gate", lambda bucket="chain", hi=False: None)
    monkeypatch.setattr(dhan_live, "_headers", lambda: {})
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200))

    dhan_live.timing_take()                      # clear anything left by another test
    dhan_live._post("/optionchain", {"x": 1})    # no window open: must not raise or accumulate
    assert dhan_live.timing_take() == {}


def _fake_idx():
    return types.SimpleNamespace(symbol="NIFTY")


def test_slow_chain_fetch_emits_timing_event_and_returns_the_same_value(monkeypatch):
    from app import data_access as da
    from data import dhan_live
    import app.obs as obs

    sentinel = ({"records": {}}, "dhan", {"open": 1.0}, None)

    def fake_fetch(idx, live_source, max_expiries=None, force_live=False):
        dhan_live._CALL_TIMING.acc["calls"] = 2  # what _post would have recorded
        dhan_live._CALL_TIMING.acc["http_ms"] = 900.0
        return sentinel

    events = []
    monkeypatch.setattr(da, "_fetch_live_chain", fake_fetch)
    monkeypatch.setattr(da, "_CHAIN_TIMING_LOG_MS", 0.0)   # every fetch counts as slow here
    monkeypatch.setattr(obs, "event", lambda name, **kw: events.append((name, kw)))

    out = da._fetch_live_chain_timed(_fake_idx(), "dhan", max_expiries=1, submitted_at=None)

    assert out is sentinel
    assert len(events) == 1
    name, kw = events[0]
    assert name == "chain.timing"
    assert kw["symbol"] == "NIFTY"
    assert kw["calls"] == 2 and kw["http_ms"] == 900.0
    assert kw["queue_ms"] is None and kw["max_expiries"] == 1


def test_fast_chain_fetch_emits_nothing(monkeypatch):
    from app import data_access as da
    import app.obs as obs

    monkeypatch.setattr(da, "_fetch_live_chain", lambda *a, **k: ({}, "dhan", None, None))
    events = []
    monkeypatch.setattr(obs, "event", lambda name, **kw: events.append(name))

    da._fetch_live_chain_timed(_fake_idx(), "dhan", submitted_at=None)
    assert events == []


def test_a_failing_fetch_still_propagates_and_clears_the_window(monkeypatch):
    from app import data_access as da
    from data import dhan_live

    def boom(*a, **k):
        raise RuntimeError("dhan down")

    monkeypatch.setattr(da, "_fetch_live_chain", boom)
    with pytest.raises(RuntimeError, match="dhan down"):
        da._fetch_live_chain_timed(_fake_idx(), "dhan", submitted_at=None)
    assert dhan_live.timing_take() == {}
