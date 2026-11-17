"""tests/test_commentary_phase8.py — Verification of Phase 8 Storage, UI & Cockpit Tab Integration."""

from __future__ import annotations

import datetime as dt
from commentary.models import CommentaryEntry, SuggestionBite
from commentary.storage.db import (
    ensure_commentary_schema,
    save_commentary_entry,
    get_recent_commentary_entries,
    get_pinned_commentary_cache,
)
from commentary.detectors.pinned import _PINNED_COMMENTARY_CACHE


def test_storage_schema_and_save_entry(tmp_path, monkeypatch):
    test_db = tmp_path / "commentary_test.db"
    import commentary.storage.db as comm_db
    monkeypatch.setattr(comm_db, "_DB_PATH", test_db)
    monkeypatch.setattr(comm_db, "_RECENT_ENTRIES_CACHE", [])
    monkeypatch.setattr(comm_db, "_CACHE_INITIALIZED", False)
    ensure_commentary_schema()
    now = dt.datetime.now(dt.timezone.utc)

    bite = SuggestionBite(
        id="bite-st-1",
        entry_id="entry-st-1",
        ts=now,
        type="ACT-IF",
        headline="Bullish Breakout",
        condition={"val": 24800.0},
        action_frame="Bull Call Spread 24800/25000",
        structure={"legs": "24800/25000"},
        levels={"trigger": 24800.0, "invalidation": 24720.0, "target": 24950.0},
        risk={"rr": 2.2, "max_loss_per_lot": 2100.0},
        valid_until=now + dt.timedelta(minutes=30),
        scenario_link="bull",
        badge={"mode": "shadow", "calibrated": False},
        facts_used=["spot", "call_wall"],
        pinned_rec_id="REC-TEST-123",
    )

    entry = CommentaryEntry(
        id="entry-st-1",
        ts=now,
        phase="morning",
        event_ids=["evt-1"],
        headline="Call Wall Tested at 24,800",
        body="Nifty is testing key call wall at 24,800.",
        bites=[bite],
        verified=True,
    )

    save_commentary_entry(entry, symbol="NIFTY")

    entries = get_recent_commentary_entries("NIFTY", limit=10)
    assert len(entries) >= 1
    found = next((e for e in entries if e["entry_id"] == "entry-st-1"), None)
    assert found is not None
    assert found["headline"] == "Call Wall Tested at 24,800"
    assert len(found["bites"]) == 1
    assert found["bites"][0]["pinned_rec_id"] == "REC-TEST-123"
    comm_db.clear_commentary_cache()


def test_same_headline_inside_one_minute_is_one_print(tmp_path, monkeypatch):
    import commentary.storage.db as comm_db
    from commentary.models import CommentaryEntry

    monkeypatch.setattr(comm_db, "_DB_PATH", tmp_path / "commentary_test.db")
    monkeypatch.setattr(comm_db, "_RECENT_ENTRIES_CACHE", [])
    monkeypatch.setattr(comm_db, "_CACHE_INITIALIZED", False)
    now = dt.datetime(2026, 11, 16, 4, 0, tzinfo=dt.timezone.utc)
    first = CommentaryEntry(
        id="alert-1",
        ts=now,
        phase="MARKET_ALERT",
        event_ids=[],
        headline="Nifty is between the walls",
        body="first",
    )
    second = CommentaryEntry(
        id="alert-2",
        ts=now + dt.timedelta(seconds=20),
        phase="MARKET_ALERT",
        event_ids=[],
        headline="Nifty is between the walls",
        body="repeat",
    )
    later = CommentaryEntry(
        id="alert-3",
        ts=now + dt.timedelta(seconds=90),
        phase="MARKET_ALERT",
        event_ids=[],
        headline="Nifty is between the walls",
        body="later",
    )
    assert save_commentary_entry(first, symbol="NIFTY") is True
    assert save_commentary_entry(second, symbol="NIFTY") is False
    assert save_commentary_entry(later, symbol="NIFTY") is True
    rows = get_recent_commentary_entries("NIFTY", limit=10)
    assert [row["entry_id"] for row in rows] == ["alert-3", "alert-1"]


def test_pinned_commentary_cache():
    _PINNED_COMMENTARY_CACHE["REC-TEST-999"] = "Target 1 approached (+42 pts)"
    cache = get_pinned_commentary_cache()
    assert "REC-TEST-999" in cache
    assert cache["REC-TEST-999"] == "Target 1 approached (+42 pts)"
