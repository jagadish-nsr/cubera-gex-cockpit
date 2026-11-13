"""SLO minute rollup, streak, VWAP blackout, tape probes."""

from __future__ import annotations

from datetime import datetime
from unittest import mock

from app.admin.clock import IST
from app.slo_monitor import (
    NOT_READY_STREAK_CRIT,
    not_ready_streak,
    probe_tape_completeness,
    probe_vwap_blackout,
    record_readiness_minute,
    reset_for_tests,
    run_session_open_smoke,
)


def setup_function():
    reset_for_tests()


def test_readiness_minute_increments_streak():
    now = datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    readiness = {
        "ready": False,
        "market_open": True,
        "reasons": ["no_fresh_chain"],
        "probes": {"feed": {}, "ingest": {}, "chains": {}, "circuits": {}},
    }
    with mock.patch("app.obs.event"):
        for i in range(NOT_READY_STREAK_CRIT):
            # unique minutes
            roll = record_readiness_minute(
                readiness,
                now=now.replace(minute=i),
            )
            assert roll["recorded"] is True
            assert roll["streak"] == i + 1
    assert not_ready_streak() == NOT_READY_STREAK_CRIT


def test_readiness_minute_resets_on_ready():
    now = datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    bad = {
        "ready": False,
        "market_open": True,
        "reasons": ["feed_not_live"],
        "probes": {"feed": {}, "ingest": {}, "chains": {}, "circuits": {}},
    }
    good = {
        "ready": True,
        "market_open": True,
        "reasons": [],
        "probes": {"feed": {}, "ingest": {}, "chains": {}, "circuits": {}},
    }
    with mock.patch("app.obs.event"):
        record_readiness_minute(bad, now=now.replace(minute=1))
        record_readiness_minute(bad, now=now.replace(minute=2))
        roll = record_readiness_minute(good, now=now.replace(minute=3))
    assert roll["streak"] == 0
    assert not_ready_streak() == 0


def test_vwap_blackout_counts_fields_json():
    rows = []
    for i in range(25):
        rows.append({
            "ts": "2099-01-01T10:00:00+05:30",  # always in window vs now_ist mock
            "message": "rec.refused",
            "fields_json": '{"reason": "Missing session VWAP — ideas are not built"}',
        })
    with mock.patch("app.admin.db.list_ops_events", return_value=rows), \
         mock.patch("app.admin.clock.now_ist", return_value=datetime(2099, 1, 1, 10, 5, tzinfo=IST)):
        out = probe_vwap_blackout(since_minutes=10)
    assert out["blackout"] is True
    assert out["count"] >= 20


def test_tape_too_early_ok():
    now = datetime(2026, 11, 13, 9, 20, tzinfo=IST)
    with mock.patch("app.slo_monitor._count_session_1m_bars", return_value=0):
        out = probe_tape_completeness(now=now, market_open=True)
    assert out["ok"] is True
    assert out["reason"] == "too_early"


def test_tape_closed_incomplete_critical_path():
    now = datetime(2026, 11, 13, 16, 0, tzinfo=IST)
    with mock.patch("app.slo_monitor._count_session_1m_bars", return_value=100):
        out = probe_tape_completeness(now=now, market_open=False)
    assert out["closed"] is True
    assert out["ok"] is False
    assert out["severity"] == "CRITICAL"


def test_session_open_smoke_off_hours():
    with mock.patch("config.is_market_open", return_value=False), \
         mock.patch("app.readiness.build_readiness", return_value={
             "ready": True,
             "market_open": False,
             "reasons": [],
             "probes": {"ingest": {"ok": True}, "chains": {}, "spots": {}, "feed": {}, "circuits": {}},
         }), \
         mock.patch("app.session_checklist.build_session_checklist", return_value={
             "pass": True, "failed": [], "ideas": {"count": 0, "reason": ""},
             "as_of": "2026-11-13T08:00:00+05:30",
         }), \
         mock.patch("app.slo_monitor.probe_vwap_blackout", return_value={
             "ok": True, "blackout": False, "count": 0,
         }), \
         mock.patch("app.slo_monitor.probe_tape_completeness", return_value={
             "ok": True, "reason": "off_session",
         }):
        out = run_session_open_smoke(version="5.3.118")
    assert out["pass"] is True
