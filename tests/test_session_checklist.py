"""Admin session-open checklist automation."""

from __future__ import annotations

from datetime import datetime
from unittest import mock

from app.admin.clock import IST
from app.session_checklist import build_session_checklist


def _probes(**overrides):
    base = {
        "chains": {},
        "spots": {},
        "feed": {"ok": False},
        "circuits": {"NIFTY": {"ok": True}},
        "token": {
            "ok": True, "present": True, "hours_left": 8.0,
            "expired": False, "reason": "",
        },
        "nse_fallback": {"active": False, "lifetime_count": 0, "ok": True, "reason": ""},
    }
    base.update(overrides)
    return base


def test_checklist_pre_open_passes_with_fresh_token():
    now = datetime(2026, 11, 13, 8, 0, tzinfo=IST)  # pre-open Friday
    with mock.patch("app.readiness.build_readiness", return_value={
        "ready": True,
        "market_open": False,
        "reasons": [],
        "degrade_mode": "OFF_SESSION",
        "probes": _probes(),
    }), mock.patch("app.session_checklist._ideas_probe", return_value={
        "ok": True, "count": 0, "summary": "", "reason": "no_batch_yet",
    }):
        payload = build_session_checklist(now=now, version="5.3.120")
    assert payload["pass"] is True
    assert payload["pre_open"] is True
    assert payload["market_open"] is False
    token_check = next(c for c in payload["checks"] if c["id"] == "token_hours")
    assert token_check["ok"] is True
    assert token_check["skipped"] is False


def test_checklist_pre_open_fails_when_token_under_2h():
    now = datetime(2026, 11, 13, 8, 30, tzinfo=IST)
    with mock.patch("app.readiness.build_readiness", return_value={
        "ready": True,
        "market_open": False,
        "reasons": [],
        "degrade_mode": "OFF_SESSION",
        "probes": _probes(token={
            "ok": False, "present": True, "hours_left": 1.2,
            "expired": False, "reason": "token_hours_1.2",
        }),
    }), mock.patch("app.session_checklist._ideas_probe", return_value={
        "ok": True, "count": 0, "summary": "", "reason": "no_batch_yet",
    }):
        payload = build_session_checklist(now=now, version="5.3.121")
    assert payload["pass"] is False
    assert "token_hours" in payload["failed"]


def test_checklist_session_fails_on_open_circuit():
    now = datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    with mock.patch("config.is_market_open", return_value=True), \
         mock.patch("app.readiness.build_readiness", return_value={
             "ready": False,
             "market_open": True,
             "reasons": ["circuit_open:NIFTY"],
             "degrade_mode": "NOT_READY",
             "probes": _probes(
                 chains={"NIFTY": {"ok": True}},
                 spots={"NIFTY": {"ok": True}},
                 feed={"ok": True, "reason": ""},
                 circuits={"NIFTY": {"ok": False}, "BANKNIFTY": {"ok": True}, "SENSEX": {"ok": True}},
             ),
         }), mock.patch("app.session_checklist._ideas_probe", return_value={
             "ok": True, "count": 2, "summary": "ok", "reason": "",
         }):
        payload = build_session_checklist(now=now, version="5.3.120")
    assert payload["pass"] is False
    assert "circuits" in payload["failed"]
    assert "readiness" in payload["failed"]


def test_checklist_flags_vwap_blackout_after_0916():
    now = datetime(2026, 11, 13, 9, 30, tzinfo=IST)
    with mock.patch("config.is_market_open", return_value=True), \
         mock.patch("app.readiness.build_readiness", return_value={
             "ready": True,
             "market_open": True,
             "reasons": [],
             "degrade_mode": "OK",
             "probes": _probes(
                 chains={"NIFTY": {"ok": True}},
                 spots={"NIFTY": {"ok": True}},
                 feed={"ok": True, "reason": ""},
                 circuits={"NIFTY": {"ok": True}, "BANKNIFTY": {"ok": True}, "SENSEX": {"ok": True}},
             ),
         }), mock.patch("app.session_checklist._ideas_probe", return_value={
             "ok": False, "count": 0, "summary": "Missing session VWAP",
             "vwap_blackout": True, "reason": "vwap_blackout",
         }):
        payload = build_session_checklist(now=now, version="5.3.120")
    assert payload["pass"] is False
    assert "ideas_vwap" in payload["failed"]


def test_checklist_flags_active_nse_fallback():
    now = datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    with mock.patch("config.is_market_open", return_value=True), \
         mock.patch("app.readiness.build_readiness", return_value={
             "ready": False,
             "market_open": True,
             "reasons": ["book_from_nse_scrape:NIFTY"],
             "degrade_mode": "CHAIN_DEGRADED",
             "probes": _probes(
                 chains={"NIFTY": {"ok": True}},
                 spots={"NIFTY": {"ok": True}},
                 feed={"ok": True, "reason": ""},
                 circuits={"NIFTY": {"ok": True}, "BANKNIFTY": {"ok": True}, "SENSEX": {"ok": True}},
                 nse_fallback={
                     "active": True, "symbols": ["NIFTY"], "lifetime_count": 3,
                     "ok": False, "reason": "book_from_nse_scrape:NIFTY",
                 },
             ),
         }), mock.patch("app.session_checklist._ideas_probe", return_value={
             "ok": True, "count": 1, "summary": "ok", "reason": "",
         }):
        payload = build_session_checklist(now=now, version="5.3.121")
    assert payload["pass"] is False
    assert "nse_fallback" in payload["failed"]
    assert payload["degrade_mode"] == "CHAIN_DEGRADED"
