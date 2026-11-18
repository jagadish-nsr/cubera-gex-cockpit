"""Hand-checkable tests for analytics/pre_open_digest.py — no network."""

from __future__ import annotations

from datetime import date, datetime

import analytics.pre_open_digest as pod
from analytics.pre_open_digest import (
    build_pre_open_digest,
    load_sealed_digest,
    resolve_pre_open_digest,
    seal_digest,
    tag_headline,
)


def test_tag_headline_maps_reliance_to_oil_gas():
    sectors, stocks = tag_headline("Reliance shares jump after oil price relief", "Markets & Economy")
    assert "RELIANCE" in stocks
    assert "Oil & Gas" in sectors or "Energy" in sectors


def test_tag_headline_maps_infosys_to_it():
    sectors, stocks = tag_headline("Infosys Q2 profit beats estimates", "Corporate & Earnings")
    assert "INFY" in stocks
    assert "IT" in sectors


def test_build_pre_open_digest_is_one_paragraph_with_sections_for_audit():
    now = datetime(2026, 6, 13, 8, 30, tzinfo=pod.IST)
    news = {
        "count": 0,
        "impact": 0,
        "top": "",
        "headlines": [
            {
                "title": "RBI rate cut sparks private bank rally",
                "url": "https://example.com/rbi",
                "pub_time_ist": "22:10 IST",
                "date_ist": "12-Jun",
                "age_min": 450,
                "impact": 4,
                "category": "Macro & Policy",
                "sentiment": "BULLISH",
                "is_fresh": False,
            },
            {
                "title": "Dow plunges as Wall Street sells off AI stocks",
                "url": "https://example.com/dow",
                "pub_time_ist": "06:05 IST",
                "date_ist": "13-Jun",
                "age_min": 145,
                "impact": 3,
                "category": "Markets & Economy",
                "sentiment": "BEARISH",
                "is_fresh": False,
            },
            {
                "title": "Court upholds $11m award after Kentucky waste spill",
                "url": "https://example.com/noise",
                "pub_time_ist": "01:00 IST",
                "date_ist": "13-Jun",
                "age_min": 450,
                "impact": 6,
                "category": "Geopolitics",
                "sentiment": "BEARISH",
                "is_fresh": False,
            },
        ],
    }
    digest = build_pre_open_digest(
        news, None, now_ist=now, session_date=date(2026, 6, 13),
        overnight_window_min=16 * 60, window_label="since prior close 15:30 IST",
    )
    assert digest["status"] == "OK"
    assert digest["event_count"] == 2
    assert "Kentucky" not in digest["narrative"]
    assert "RBI" in digest["narrative"] or "Dow" in digest["narrative"]
    assert "Real-Time News" in digest["narrative"]
    assert digest["narrative"] == digest["one_liner"]
    assert any(s["key"] == "us_global" for s in digest["sections"]) or any(
        s["key"] == "india" for s in digest["sections"]
    )


def test_seal_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(pod, "JOURNAL_DIR", tmp_path)
    now = datetime(2026, 6, 13, 9, 5, tzinfo=pod.IST)
    news = {
        "headlines": [{
            "title": "Iran missile strike rattles oil markets",
            "url": "https://example.com/iran",
            "age_min": 120,
            "impact": 5,
            "category": "Geopolitics",
            "sentiment": "BEARISH",
            "pub_time_ist": "07:00 IST",
        }],
    }
    built = build_pre_open_digest(
        news, None, now_ist=now, session_date=date(2026, 6, 13),
        overnight_window_min=960, window_label="test",
    )
    sealed = seal_digest(built, date(2026, 6, 13))
    assert sealed["sealed"] is True
    again = load_sealed_digest(date(2026, 6, 13))
    assert again is not None and again["event_count"] == 1
    # resolve after open prefers seal
    got = resolve_pre_open_digest(
        {"headlines": []}, None, now_ist=now, session_phase="REGULAR_SESSION",
    )
    assert got["sealed"] is True
    assert "Iran" in (got.get("narrative") or "")
