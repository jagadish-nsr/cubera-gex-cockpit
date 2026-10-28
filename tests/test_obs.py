"""Structured logs, workflow events, HTTP counters, and stale-process alerts."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, Response

import data.holidays as holidays
from app.admin import db
from app.admin.clock import IST
from app.admin.health_daemon import evaluate_alerts
from app.admin.routes import register_admin_routes
from app.obs import (
    bind,
    book_fields,
    configure,
    current,
    event,
    heartbeat,
    install,
    observe_request,
    process_name,
    redact_mapping,
    redact_text,
    reset,
)
from app.obs import metrics


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    cache: dict[int, set] = {}

    def load(year: int):
        if year not in cache:
            from datetime import date
            found = set()
            names = {}
            for iso, name in holidays.NSE_HOLIDAY_NAMES.items():
                day = date.fromisoformat(iso)
                if day.year == year:
                    found.add(day)
                    names[day] = name
            cache[year] = found
            holidays._calendar_names_cache[year] = names
        return cache[year]

    monkeypatch.setattr(holidays, "_load_or_fetch", load)
    db.set_db_path(tmp_path / "admin_ops.db")
    reset(tmp_path)
    metrics.reset()
    configure("test", log_dir=tmp_path)
    yield tmp_path
    reset()
    metrics.reset()
    db.set_db_path(None)


def _asgi(app, method: str, path: str, query: str = ""):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [],
    }
    body = []
    status = [None]
    headers = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status[0] = message["status"]
            for key, value in message.get("headers", []):
                headers[key.decode().lower()] = value.decode()
        elif message["type"] == "http.response.body":
            body.append(message.get("body", b""))

    asyncio.run(app(scope, receive, send))
    return status[0], headers, b"".join(body)


def _handlers():
    return [h for h in logging.getLogger().handlers if getattr(h, "_cubera_obs", False)]


def _token(hours: float) -> dict:
    return {"present": True, "hours_left": hours, "expired": False, "source": "file"}


def _feed() -> dict:
    return {"ws_connected": True, "last_tick_age_s": 1.0, "is_live": True}


def test_redaction_drops_token_and_bearer():
    hidden = redact_mapping({"token": "super-secret", "symbol": "NIFTY"})
    assert hidden["token"] == "[redacted]"
    assert hidden["symbol"] == "NIFTY"
    bearer = redact_text("Authorization: Bearer abcdefghijklmnop")
    assert "abcdefghijklmnop" not in bearer
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    assert "eyJ" not in redact_text(jwt)


def test_configure_is_idempotent_and_writes_one_json_line(tmp_path):
    before = len(_handlers())
    configure("other", log_dir=tmp_path)
    logging.getLogger("cubera.probe").info("hello")
    assert len(_handlers()) == before
    for handler in _handlers():
        handler.flush()
    lines = (tmp_path / "test.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["msg"] == "hello"
    assert payload["process"] == "test"
    assert "ts" in payload


def test_event_keeps_lot_and_sign_and_drops_the_token():
    trace = event(
        "chain.published",
        symbol="NIFTY",
        contract_size=65,
        dealer_sign_model="short_call_short_put",
        token="secret-value",
        message="saw Bearer abcdefghijklmnop",
    )
    rows = db.list_ops_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["trace_id"] == trace
    assert row["fields"]["contract_size"] == 65
    assert row["fields"]["dealer_sign_model"] == "short_call_short_put"
    assert row["fields"]["token"] == "[redacted]"
    blob = json.dumps(row)
    assert "secret-value" not in blob
    assert "abcdefghijklmnop" not in blob
    for handler in _handlers():
        handler.flush()


def test_book_fields_name_the_sign():
    class Levels:
        spot = 24000.0
        call_wall = 24200.0
        put_wall = 23800.0
        flip = 24100.0
        total_net_gex = -5.0
        dealer_sign_model = "short_call_short_put"
        oi_timestamp = "2026-10-27T10:00:00+05:30"
        oi_stale = False

    class Book:
        symbol = "NIFTY"
        levels = Levels()

    import config
    fields = book_fields(Book())
    assert fields["sign"] == "put-heavy"
    assert fields["contract_size"] == config.INDICES["NIFTY"].lot_size
    assert fields["dealer_sign_model"] == "short_call_short_put"
    assert "per_strike" not in fields


def test_trace_id_does_not_cross_threads():
    seen = {}

    def worker():
        seen["trace"] = current()["trace_id"]

    with bind(trace_id="abc123"):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert current()["trace_id"] == "abc123"
    assert seen["trace"] is None


def test_fast_http_200_is_a_counter_and_a_500_is_an_event():
    app = FastAPI()
    install(app)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    @app.get("/bad")
    def bad():
        return Response(status_code=500)

    status, headers, _body = _asgi(app, "GET", "/ok")
    assert status == 200
    assert headers.get("x-request-id")
    assert db.list_ops_events(event="http.error") == []
    assert metrics.snapshot()["by_class"]["2xx"] == 1

    observe_request("GET", "/static/js/admin.js", 200, 1.0, "asset")
    assert metrics.snapshot()["count"] == 1

    observe_request("GET", "/api/cockpit-data", 200, 12.0, "poll")
    assert db.list_ops_events() == []
    assert metrics.snapshot()["count"] == 2

    _asgi(app, "GET", "/bad")
    rows = db.list_ops_events(event="http.error")
    assert len(rows) == 1
    assert rows[0]["level"] == "ERROR"
    assert metrics.snapshot()["by_class"]["5xx"] == 1


def test_prune_drops_events_older_than_seven_days(tmp_path):
    old = (datetime.now(IST) - timedelta(days=8)).isoformat(timespec="seconds")
    db.insert_ops_event({
        "ts": old,
        "process": "test",
        "event": "old.row",
        "level": "INFO",
        "trace_id": "old",
        "symbol": None,
        "duration_ms": None,
        "message": "old",
        "fields_json": "{}",
    })
    event("fresh.row", message="fresh")
    now = datetime.now(IST)
    evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=_feed,
        include_gaps=False,
    )
    names = [row["event"] for row in db.list_ops_events(limit=20)]
    assert "old.row" not in names
    assert "fresh.row" in names


def test_stale_heartbeat_alerts_only_for_a_process_that_beat_today(tmp_path):
    now = datetime(2026, 10, 23, 12, 0, tzinfo=IST)
    quiet = evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=_feed,
        include_gaps=False,
    )
    assert quiet["badge_count"] == 0

    yesterday = datetime(2026, 10, 22, 12, 0, tzinfo=IST)
    db.upsert_ops_status(
        process="replay",
        name="heartbeat",
        now_iso=yesterday.isoformat(timespec="seconds"),
        beat=True,
        pid=9,
    )
    still = evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=_feed,
        include_gaps=False,
    )
    assert still["badge_count"] == 0

    stale_at = datetime(2026, 10, 23, 11, 56, tzinfo=IST)
    db.upsert_ops_status(
        process="web",
        name="heartbeat",
        now_iso=stale_at.isoformat(timespec="seconds"),
        beat=True,
        pid=42,
    )
    raised = evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=_feed,
        include_gaps=False,
    )
    assert raised["badge_count"] == 1
    rows = db.list_alerts()
    assert len(rows) == 1
    assert rows[0]["fingerprint"] == "PROCESS|web"
    assert rows[0]["severity"] == "WARNING"

    fresh = datetime(2026, 10, 23, 11, 58, tzinfo=IST)
    db.upsert_ops_status(
        process="web",
        name="heartbeat",
        now_iso=fresh.isoformat(timespec="seconds"),
        beat=True,
        pid=42,
    )
    cleared = evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=_feed,
        include_gaps=False,
    )
    assert cleared["badge_count"] == 0
    assert db.list_alerts() == []


def test_admin_ops_route_returns_events_and_heartbeats():
    event(
        "chain.published",
        symbol="NIFTY",
        contract_size=65,
        dealer_sign_model="short_call_short_put",
    )
    heartbeat()
    app = FastAPI()
    register_admin_routes(app)
    status, _headers, raw = _asgi(app, "GET", "/api/admin/ops", "event=chain.published&limit=10")
    assert status == 200
    body = json.loads(raw)
    assert body["events"][0]["event"] == "chain.published"
    assert body["events"][0]["fields"]["dealer_sign_model"] == "short_call_short_put"
    assert any(row["name"] == "heartbeat" and row["process"] == process_name() for row in body["heartbeats"])
    assert any(row["name"] == "chain.published" for row in body["workflows"])
    _status, _headers, empty_raw = _asgi(app, "GET", "/api/admin/ops", "process=missing")
    assert json.loads(empty_raw)["events"] == []
