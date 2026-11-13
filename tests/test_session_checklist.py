"""Admin session-open checklist automation."""

from __future__ import annotations

from datetime import datetime
from unittest import mock

from app.admin.clock import IST
from app.session_checklist import build_session_checklist


def test_checklist_off_session_passes_without_live_feed():
    now = datetime(2026, 11, 13, 8, 0, tzinfo=IST)  # pre-open
    with mock.patch("app.readiness.build_readiness", return_value={
        "ready": True,
        "market_open": False,
        "reasons": [],
        "probes": {
            "chains": {},
            "spots": {},
            "feed": {"ok": False},
            "circuits": {"NIFTY": {"ok": True}},
        },
    }), mock.patch("app.session_checklist._ideas_probe", return_value={
        "ok": True, "count": 0, "summary": "", "reason": "no_batch_yet",
    }):
        payload = build_session_checklist(now=now, version="5.3.120")
    assert payload["pass"] is True
    assert payload["market_open"] is False


def test_checklist_session_fails_on_open_circuit():
    now = datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    with mock.patch("config.is_market_open", return_value=True), \
         mock.patch("app.readiness.build_readiness", return_value={
             "ready": False,
             "market_open": True,
             "reasons": ["circuit_open:NIFTY"],
             "probes": {
                 "chains": {"NIFTY": {"ok": True}},
                 "spots": {"NIFTY": {"ok": True}},
                 "feed": {"ok": True, "reason": ""},
                 "circuits": {"NIFTY": {"ok": False}, "BANKNIFTY": {"ok": True}, "SENSEX": {"ok": True}},
             },
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
             "probes": {
                 "chains": {"NIFTY": {"ok": True}},
                 "spots": {"NIFTY": {"ok": True}},
                 "feed": {"ok": True, "reason": ""},
                 "circuits": {"NIFTY": {"ok": True}, "BANKNIFTY": {"ok": True}, "SENSEX": {"ok": True}},
             },
         }), mock.patch("app.session_checklist._ideas_probe", return_value={
             "ok": False, "count": 0, "summary": "Missing session VWAP",
             "vwap_blackout": True, "reason": "vwap_blackout",
         }):
        payload = build_session_checklist(now=now, version="5.3.120")
    assert payload["pass"] is False
    assert "ideas_vwap" in payload["failed"]
