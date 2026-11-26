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


def _hl(title, age_min, impact=5):
    return {"title": title, "url": "https://example.com/x", "age_min": age_min, "impact": impact,
            "category": "Markets & Economy", "sentiment": "NEUTRAL", "pub_time_ist": ""}


def test_late_build_keeps_only_headlines_from_before_the_open(tmp_path, monkeypatch):
    """26-Nov: first sealed at 13:13 with 'Sensex jumped 350 points' in the pre-open digest."""
    monkeypatch.setattr(pod, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr("analytics.llm_client.llm_enabled", lambda *_a, **_k: False)
    now = datetime(2026, 11, 26, 13, 13, tzinfo=pod.IST)
    # 13:13 − 09:15 = 238 min. Age 400 → 06:33 (keep); age 120 → 11:13 (drop).
    news = {"headlines": [
        _hl("Nasdaq plunges as tariff fears hit tech stocks", 400),
        _hl("Sensex jumps 350 points as IT stocks rally", 120),
    ]}
    got = resolve_pre_open_digest(news, None, now_ist=now, session_phase="REGULAR_SESSION")
    titles = [e["title"] for e in got["top_events"]]
    assert titles == ["Nasdaq plunges as tariff fears hit tech stocks"]
    assert got["sealed"] is True and got["sealed_late"] is True
    assert got["as_of_ist"] == "09:15 IST"


def test_late_build_with_only_intraday_headlines_seals_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(pod, "JOURNAL_DIR", tmp_path)
    now = datetime(2026, 11, 26, 13, 13, tzinfo=pod.IST)
    got = resolve_pre_open_digest({"headlines": [_hl("Sensex jumps 350 points as IT stocks rally", 120)]},
                                  None, now_ist=now, session_phase="REGULAR_SESSION")
    assert got["event_count"] == 0
    assert load_sealed_digest(date(2026, 11, 26)) is None


def test_sealer_seals_in_pre_open_without_a_ui_request(tmp_path, monkeypatch):
    monkeypatch.setattr(pod, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr("analytics.llm_client.llm_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr("data.pre_post_market.get_current_session_phase",
                        lambda *a, **k: ("PRE_MARKET_ENTRY", "", 0))
    monkeypatch.setattr("data.news.fetch_news",
                        lambda *a, **k: {"headlines": [_hl("Nasdaq plunges as tariff fears hit tech stocks", 200)]})
    monkeypatch.setattr("data.sectors.fetch_sector_snap", lambda *a, **k: None)

    class _Stop(Exception):
        pass

    def _sleep(_s):
        raise _Stop

    monkeypatch.setattr("time.sleep", _sleep)
    try:
        pod._seal_loop()
    except _Stop:
        pass
    today = datetime.now(pod.IST).date()
    sealed = load_sealed_digest(today)
    assert sealed is not None and sealed["event_count"] == 1
