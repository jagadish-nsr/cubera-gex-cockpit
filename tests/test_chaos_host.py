"""Pillar G — chaos host fault injection (no live DB writes, no httpx)."""

from __future__ import annotations

import asyncio
import json

from app.chaos.host import app, assert_no_live_db_writes
from app.chaos.state import reset_faults


async def _asgi(method: str, path: str, body=None):
    body_bytes = json.dumps(body).encode() if body is not None else b""
    headers = []
    if body is not None:
        headers.append([b"content-type", b"application/json"])
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
    }
    receive_state = {"done": False}

    async def receive():
        if not receive_state["done"]:
            receive_state["done"] = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        return {"type": "http.disconnect"}

    status = [None]
    chunks: list[bytes] = []

    async def send(message):
        if message["type"] == "http.response.start":
            status[0] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return status[0], json.loads(b"".join(chunks).decode() or "{}")


def setup_function() -> None:
    reset_faults()


def test_forbidden_live_db_paths_listed():
    paths = assert_no_live_db_writes()
    assert any(p.endswith("sentinel.db") for p in paths)
    assert any("paper_trades.db" in p for p in paths)


def test_health_marks_chaos_env():
    status, data = asyncio.run(_asgi("GET", "/api/health"))
    assert status == 200
    assert data["env"] == "chaos"
    assert data["readiness"]["chaos"] is True


def test_empty_chain_fails_checklist():
    asyncio.run(_asgi("POST", "/api/chaos/reset"))
    status, inj = asyncio.run(
        _asgi("POST", "/api/chaos/inject", {"empty_chain": True, "force_market_open": True})
    )
    assert status == 200
    cl = inj["checklist"]
    assert cl["pass"] is False
    assert "chain" in cl["failed"]
    assert "readiness" in cl["failed"]
    assert cl["readiness"]["degrade_mode"] == "CHAIN_DEGRADED"


def test_feed_silence_fails_checklist():
    asyncio.run(_asgi("POST", "/api/chaos/reset"))
    _, inj = asyncio.run(
        _asgi("POST", "/api/chaos/inject", {"feed_silence": True, "force_market_open": True})
    )
    cl = inj["checklist"]
    assert cl["pass"] is False
    assert "feed" in cl["failed"]
    assert cl["readiness"]["degrade_mode"] == "FEED_DOWN"


def test_cb_open_and_vwap_and_stale_spot():
    for fault, expect in (
        ("cb_open", "circuits"),
        ("stale_spot", "spot"),
        ("missing_vwap", "ideas_vwap"),
    ):
        asyncio.run(_asgi("POST", "/api/chaos/reset"))
        _, inj = asyncio.run(
            _asgi("POST", "/api/chaos/inject", {fault: True, "force_market_open": True})
        )
        cl = inj["checklist"]
        assert cl["pass"] is False, fault
        assert expect in cl["failed"], (fault, cl["failed"])


def test_reset_clears_faults():
    asyncio.run(_asgi("POST", "/api/chaos/inject", {"empty_chain": True, "force_market_open": True}))
    asyncio.run(_asgi("POST", "/api/chaos/reset"))
    _, data = asyncio.run(_asgi("GET", "/api/chaos/faults"))
    assert data["faults"]["empty_chain"] is False
    assert data["faults"]["force_market_open"] is False
