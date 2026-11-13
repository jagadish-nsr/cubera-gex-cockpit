"""Deep /api/health readiness probes — fail loud when session data is stale."""

from __future__ import annotations

import time
from unittest import mock

from app.readiness import build_readiness, CHAIN_STALE_S


def _fresh_token(**kw):
    base = {
        "present": True, "hours_left": 8.0, "expired": False,
        "source": "file", "malformed": False,
    }
    base.update(kw)
    return base


def test_readiness_off_session_is_ready():
    payload = build_readiness(market_open=False)
    assert payload["ready"] is True
    assert payload["market_open"] is False
    assert payload["reasons"] == []
    assert payload["degrade_mode"] == "OFF_SESSION"
    assert "token" in payload["probes"]
    assert "spot_source" in payload["probes"]


def test_readiness_session_fails_without_chain_or_spot():
    with mock.patch("app.live_fetcher.latest", return_value=None), \
         mock.patch("app.spot_ticker._STORE", {}), \
         mock.patch("app.ws_spot_feed.get_status", return_value={
             "ws_connected": False, "is_live": False, "last_tick_age_s": None, "ws_error": None,
         }), \
         mock.patch("data.dhan.token_status", return_value=_fresh_token()), \
         mock.patch("resilience.get_circuit_breaker") as cb_fn:
        class _CB:
            state = type("S", (), {"value": "CLOSED"})()
            consecutive_failures = 0
            cooldown_remaining = 0.0
        cb_fn.return_value = _CB()
        payload = build_readiness(market_open=True)
    assert payload["ready"] is False
    assert "no_fresh_chain" in payload["reasons"]
    assert "no_fresh_spot" in payload["reasons"]
    assert payload["degrade_mode"] in ("FEED_DOWN", "CHAIN_DEGRADED", "NOT_READY")


def test_readiness_token_dead_mode():
    with mock.patch("app.live_fetcher.latest", return_value=None), \
         mock.patch("app.spot_ticker._STORE", {}), \
         mock.patch("app.ws_spot_feed.get_status", return_value={
             "ws_connected": True, "is_live": True, "last_tick_age_s": 1.0, "ws_error": None,
         }), \
         mock.patch("data.dhan.token_status", return_value=_fresh_token(
             present=False, hours_left=0.0, expired=True,
         )), \
         mock.patch("resilience.get_circuit_breaker") as cb_fn:
        class _CB:
            state = type("S", (), {"value": "CLOSED"})()
            consecutive_failures = 0
            cooldown_remaining = 0.0
        cb_fn.return_value = _CB()
        payload = build_readiness(market_open=True)
    assert payload["degrade_mode"] == "TOKEN_DEAD"
    assert payload["probes"]["token"]["ok"] is False


def test_readiness_stale_chain_flagged():
    class _Dash:
        is_degraded = False
        degraded_reason = ""
        source = "dhan"

    stale_rec = {"data": _Dash(), "fetched": time.time() - (CHAIN_STALE_S + 30)}
    with mock.patch("app.live_fetcher.latest", return_value=stale_rec), \
         mock.patch("app.spot_ticker._STORE", {
             "NIFTY": {"at": time.time(), "quote": {"spot": 25000.0}, "ohlc": {"ltp": 25000.0}, "source": "ws"},
             "BANKNIFTY": {"at": time.time(), "quote": {"spot": 52000.0}, "ohlc": {"ltp": 52000.0}, "source": "ws"},
             "SENSEX": {"at": time.time(), "quote": {"spot": 81000.0}, "ohlc": {"ltp": 81000.0}, "source": "ws"},
         }), \
         mock.patch("app.ws_spot_feed.get_status", return_value={
             "ws_connected": True, "is_live": True, "last_tick_age_s": 1.0, "ws_error": None,
         }), \
         mock.patch("data.dhan.token_status", return_value=_fresh_token()), \
         mock.patch("resilience.get_circuit_breaker") as cb_fn:
        class _CB:
            state = type("S", (), {"value": "CLOSED"})()
            consecutive_failures = 0
            cooldown_remaining = 0.0
        cb_fn.return_value = _CB()
        payload = build_readiness(market_open=True)
    assert payload["probes"]["chains"]["NIFTY"]["ok"] is False
    assert "no_fresh_chain" in payload["reasons"] or not payload["ready"]
    assert payload["degrade_mode"] == "CHAIN_DEGRADED"


def test_health_endpoint_includes_readiness():
    import asyncio
    import json
    from tests.test_web_server_endpoints import asgi_request

    status, _, content = asyncio.run(asgi_request("GET", "/api/health"))
    assert status == 200
    data = json.loads(content)
    assert "ready" in data
    assert "readiness" in data
    assert "probes" in data["readiness"]
    assert "chains" in data["readiness"]["probes"]
    assert "degrade_mode" in data["readiness"]
