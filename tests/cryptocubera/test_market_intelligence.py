"""Market intelligence: schema, classify, score, calendar, blackout, pipeline."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["CRYPTO_DB_URL"] = "sqlite:///:memory:"
os.environ.pop("CRYPTOPANIC_TOKEN", None)
os.environ.pop("CRYPTO_LIVE_FEEDS", None)

from crypto.config import get_flag, list_flags, load_config, set_flag
from crypto.engine.signals import build_signal_card
from crypto.engine.gex import compute_gex_features
from crypto.intel.blackout import blackout_active, blackout_summary
from crypto.intel.calendar import build_calendar_stubs
from crypto.intel.classify import classify_headline, novelty_against
from crypto.intel.pipeline import (
    enrich_event,
    intel_to_news_event,
    run_intel_pipeline,
)
from crypto.intel.schema import IntelEvent, tier_for_score
from crypto.intel.score import compute_impact_score, score_and_tier
from crypto.providers.cryptopanic.news import CryptoPanicNewsProvider
from crypto.providers.mock.chain import MockOptionChainProvider
from crypto.providers.mock.news import MockNewsProvider
from crypto.providers.mock.perp import MockPerpProvider
from crypto.service import CockpitService
from crypto.store.db import get_engine, init_db
from crypto.store.schema import EventRow
from crypto.store.writer import StoreWriter
from sqlalchemy import select
from crypto.store.db import get_session


NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 'intel.db'}")
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    load_config(reload=True)
    set_flag("intel.news", True, actor="test")
    set_flag("intel.calendar", True, actor="test")
    get_engine(reload=True)
    init_db(reload=True)
    yield
    load_config(reload=True)


def test_flags_include_intel():
    flags = list_flags()
    assert flags["intel.news"] is True
    assert flags["intel.calendar"] is True


def test_classify_hack_and_assets():
    out = classify_headline("Major exchange hack drains BTC wallets on Binance")
    assert out["event_type"] == "hack"
    assert "BTC" in out["assets"]
    assert "binance" in out["venues"]


def test_classify_macro_fomc():
    out = classify_headline("FOMC holds rates; crypto watches CPI next")
    assert out["event_type"] == "macro"


def test_novelty_near_duplicate():
    a = "BTC ETF sees large inflow from BlackRock"
    b = "BTC ETF sees large inflow from Blackrock funds"
    assert novelty_against(b, [a]) < 0.6


def test_impact_score_and_tiers():
    high = compute_impact_score(
        source_reliability=0.92, novelty=1.0, prior_move_proxy=0.85
    )
    assert high >= 0.72
    assert tier_for_score(high) == "BREAKING"
    assert tier_for_score(0.4) == "WATCH"
    assert tier_for_score(0.1, scheduled_imminent=True) == "IMMINENT"


def test_calendar_stub_imminent_not_blackout_at_45m():
    stubs = build_calendar_stubs(NOW)
    cpi = next(e for e in stubs if e.event_id == "cal-cpi-stub")
    assert cpi.occurs_at == NOW + timedelta(minutes=45)
    scored = score_and_tier(cpi, NOW)
    assert scored.tier == "IMMINENT"
    # 45m out → outside 30m hard-veto window
    assert blackout_active([scored], NOW, window_minutes=30) is False


def test_calendar_near_blackout_vetoes():
    stubs = build_calendar_stubs(NOW, include_near_blackout=True)
    scored = [score_and_tier(e, NOW) for e in stubs]
    summary = blackout_summary(scored, NOW, window_minutes=30)
    assert summary["active"] is True
    assert any("maint-delta-now" in r for r in summary["reasons"])


def test_blackout_active_window_covering_now():
    ev = IntelEvent(
        event_id="win-1",
        title="Delta maintenance",
        source="delta-status-stub",
        tier="IMMINENT",
        score=0.8,
        event_type="maintenance",
        blackout_start=NOW - timedelta(minutes=5),
        blackout_until=NOW + timedelta(minutes=25),
        occurs_at=NOW + timedelta(minutes=10),
    )
    assert blackout_active([ev], NOW) is True


def test_pipeline_mock_default_offline():
    provider = MockNewsProvider()
    result = asyncio.run(run_intel_pipeline(provider, now=NOW, include_calendar=True))
    events = result["events"]
    assert len(events) >= 3
    tiers = {e.tier for e in events}
    assert "IMMINENT" in tiers  # CPI stub at +45m
    # Default stubs keep blackout off so GO demo still works
    assert result["blackout"]["active"] is False
    hack = next(e for e in events if e.event_id == "fx-btc-hack-alert")
    assert hack.event_type == "hack"
    assert hack.tier == "BREAKING"


def test_pipeline_flag_off_empty():
    set_flag("intel.news", False, actor="test")
    result = asyncio.run(run_intel_pipeline(MockNewsProvider(), now=NOW))
    assert result["events"] == []
    assert result["blackout"]["active"] is False


def test_cryptopanic_without_token_uses_mock():
    p = CryptoPanicNewsProvider(token="")
    assert p.live_enabled is False
    events = asyncio.run(p.fetch_events())
    assert len(events) >= 1
    assert any("hack" in e.title.lower() or "funding" in e.title.lower() for e in events)


def test_persist_events_extended_columns():
    provider = MockNewsProvider()
    result = asyncio.run(run_intel_pipeline(provider, now=NOW, include_calendar=False))
    writer = StoreWriter()
    n = writer.write_events([intel_to_news_event(e) for e in result["events"]])
    assert n >= 1
    with get_session() as s:
        rows = s.execute(select(EventRow)).scalars().all()
        assert rows
        assert rows[0].event_type is not None
        assert "assets" in (rows[0].payload or {})


def test_signal_veto_on_real_blackout():
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    perp = asyncio.run(MockPerpProvider().fetch_perp())
    gex = compute_gex_features(snap)
    # Without blackout → fixture GO
    go = build_signal_card(gex, perp, news_blackout=False)
    assert go["status"] == "GO"
    # With blackout → NO-GO
    nogo = build_signal_card(gex, perp, news_blackout=True)
    assert nogo["status"] == "NO-GO"
    assert any("blackout" in r.lower() for r in nogo["veto_reasons"])


def test_pipeline_near_blackout_feeds_veto():
    result = asyncio.run(
        run_intel_pipeline(
            MockNewsProvider(),
            now=NOW,
            include_calendar=True,
            include_near_blackout=True,
        )
    )
    assert result["blackout"]["active"] is True
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    perp = asyncio.run(MockPerpProvider().fetch_perp())
    gex = compute_gex_features(snap)
    card = build_signal_card(gex, perp, news_blackout=True)
    assert card["status"] == "NO-GO"


def test_cockpit_snapshot_intel_section():
    svc = CockpitService()
    data = asyncio.run(svc.snapshot())
    assert data["intel"]["flag"] is True
    assert isinstance(data["news"], list)
    assert len(data["news"]) >= 1
    assert "blackout" in data["intel"]
    # Default calendar keeps blackout inactive
    assert data["intel"]["blackout"]["active"] is False
    row = data["news"][0]
    assert "tier" in row and "event_type" in row and "score" in row
