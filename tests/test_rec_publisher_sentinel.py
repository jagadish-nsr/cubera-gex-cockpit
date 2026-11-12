"""Regression: Trade Ideas publisher must supply Sentinel VWAP bars.

12-Nov-2026: harden correctly refused missing VWAP, but the publisher never
passed ``latest_bar`` → ~6.8k refuses and zero Ideas all session.
"""

from __future__ import annotations

from types import SimpleNamespace

import web_server


def test_load_rec_sentinel_context_returns_latest_bar(monkeypatch):
    bar = SimpleNamespace(
        vwap=25400.0,
        vwap_sigma_1=25440.0,
        vwap_lower_1=25360.0,
        close=25410.0,
    )
    sig = SimpleNamespace(type="ABSORPTION_BULL", score=72)

    def _bars(inst_id, **kw):
        assert inst_id == "NIFTY-FUT"
        assert kw.get("timeframe") == "1m"
        return [bar]

    def _sigs(inst_id, limit=12):
        assert inst_id == "NIFTY-FUT"
        return [sig]

    monkeypatch.setattr(web_server, "list_recent_bars", _bars)
    monkeypatch.setattr(web_server, "list_recent_signals", _sigs)

    latest, bars, sigs = web_server._load_rec_sentinel_context("NIFTY")
    assert latest is bar
    assert bars == [bar]
    assert sigs == [sig]
    assert float(latest.vwap) > 0
    assert float(latest.vwap_sigma_1) > 0


def test_load_rec_sentinel_context_empty_when_no_bars(monkeypatch):
    monkeypatch.setattr(web_server, "list_recent_bars", lambda *a, **k: [])
    monkeypatch.setattr(web_server, "list_recent_signals", lambda *a, **k: [])
    latest, bars, sigs = web_server._load_rec_sentinel_context("SENSEX")
    assert latest is None
    assert bars == []
    assert sigs == []


def test_generate_without_latest_bar_refuses_vwap_not_invented():
    """Contract: chain dash without Sentinel bar → VWAP refuse (12-Nov publisher bug)."""
    from analytics.trade_ideas import generate_trade_recommendations
    from tests.test_trade_ideas import MockDashboardData

    data = MockDashboardData(spot=25400.0)
    data.latest_bar = None  # live chain object has no VWAP; publisher must supply it
    batch = generate_trade_recommendations(data, latest_bar=None, min_score=0)
    summary = str(getattr(batch, "executive_summary", "") or "")
    assert "Missing session VWAP" in summary
    ideas = []
    for attr in (
        "ce_recommendations",
        "pe_recommendations",
        "tactical_ce_recommendations",
        "tactical_pe_recommendations",
    ):
        ideas.extend(list(getattr(batch, attr, None) or []))
    assert ideas == []


def test_generate_with_publisher_context_clears_vwap_gate(monkeypatch):
    """Publisher-shaped kwargs (latest_bar + bars) must not trip the VWAP refuse."""
    from analytics.trade_ideas import generate_trade_recommendations
    from tests.test_trade_ideas import MockDashboardData, _mock_latest_bar

    data = MockDashboardData(spot=25400.0)
    bar = _mock_latest_bar(spot=25400.0)
    batch = generate_trade_recommendations(
        data,
        latest_bar=bar,
        sentinel_bars=[bar],
        sentinel_signals=[],
        min_score=0,
    )
    summary = str(getattr(batch, "executive_summary", "") or "")
    assert "Missing session VWAP" not in summary
