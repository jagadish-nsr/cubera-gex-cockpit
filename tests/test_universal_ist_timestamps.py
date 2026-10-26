"""tests/test_universal_ist_timestamps.py — Strict regression tests for universal IST timestamp handling."""

from __future__ import annotations

import datetime as dt
from commentary.models import (
    IST,
    to_ist,
    now_ist,
    PointInTimeView,
    Event,
    SuggestionBite,
    CommentaryEntry,
)
from commentary.story_state import StoryStateManager
from commentary.storage.db import (
    ensure_commentary_schema,
    save_commentary_entry,
    get_recent_commentary_entries,
    clear_commentary_cache,
)


def test_to_ist_conversions():
    # 1. UTC aware datetime
    utc_dt = dt.datetime(2026, 10, 13, 3, 56, 4, tzinfo=dt.timezone.utc)
    ist_res = to_ist(utc_dt)
    assert ist_res.tzinfo == IST
    assert ist_res.hour == 9
    assert ist_res.minute == 26
    assert ist_res.second == 4

    # 2. Naive UTC datetime during trading hours (03:56)
    naive_utc = dt.datetime(2026, 10, 13, 3, 56, 4)
    ist_res_naive = to_ist(naive_utc)
    assert ist_res_naive.tzinfo == IST
    assert ist_res_naive.hour == 9
    assert ist_res_naive.minute == 26

    # 3. Naive IST datetime (09:26)
    naive_ist = dt.datetime(2026, 10, 13, 9, 26, 4)
    ist_res_ist = to_ist(naive_ist)
    assert ist_res_ist.tzinfo == IST
    assert ist_res_ist.hour == 9
    assert ist_res_ist.minute == 26

    # 4. UTC ISO string with 'Z'
    str_z = "2026-10-13T03:56:04Z"
    ist_str_z = to_ist(str_z)
    assert ist_str_z.tzinfo == IST
    assert ist_str_z.hour == 9
    assert ist_str_z.minute == 26

    # 5. UTC ISO string with '+00:00'
    str_offset = "2026-10-13T03:56:04+00:00"
    ist_str_offset = to_ist(str_offset)
    assert ist_str_offset.tzinfo == IST
    assert ist_str_offset.hour == 9
    assert ist_str_offset.minute == 26

    # 6. None returns current IST time
    none_res = to_ist(None)
    assert none_res.tzinfo == IST


def test_models_auto_normalize_to_ist():
    utc_dt = dt.datetime(2026, 10, 13, 3, 56, 4, tzinfo=dt.timezone.utc)

    # PointInTimeView
    pit = PointInTimeView(
        as_of=utc_dt,
        symbol="NIFTY",
        spot=22600.0,
        prior_close=22550.0,
        atm_iv=0.14,
        expected_move_1d=100.0,
        call_wall=22700.0,
        put_wall=22500.0,
        gamma_flip=22600.0,
        max_pain=22600.0,
        net_gex_sign=1,
    )
    assert pit.as_of.tzinfo == IST
    assert pit.as_of.hour == 9

    # Event
    ev = Event(
        id="evt-test-1",
        ts=utc_dt,
        type="WALL_TEST",
        facts={"strike": 22600.0},
        magnitude_sigma=0.5,
    )
    assert ev.ts.tzinfo == IST
    assert ev.ts.hour == 9

    # SuggestionBite
    bite = SuggestionBite(
        id="bite-1",
        entry_id="entry-1",
        ts=utc_dt,
        type="WATCH",
        headline="Test Watch",
        condition={},
        action_frame="Call",
        structure={},
        levels={},
        risk={},
        valid_until=utc_dt + dt.timedelta(minutes=30),
        scenario_link="bull",
        badge={},
        facts_used=[],
        resolved_ts=utc_dt + dt.timedelta(minutes=15),
    )
    assert bite.ts.tzinfo == IST
    assert bite.valid_until.tzinfo == IST
    assert bite.resolved_ts.tzinfo == IST
    assert bite.ts.hour == 9

    # CommentaryEntry
    entry = CommentaryEntry(
        id="entry-1",
        ts=utc_dt,
        phase="morning",
        event_ids=["evt-test-1"],
        headline="Test Headline",
        body="Test Body",
    )
    assert entry.ts.tzinfo == IST
    assert entry.ts.hour == 9


def test_story_state_phase_with_utc():
    sm = StoryStateManager()
    # 03:56 UTC is 09:26 IST (morning)
    utc_dt = dt.datetime(2026, 10, 13, 3, 56, 4, tzinfo=dt.timezone.utc)
    phase = sm.determine_phase(utc_dt)
    assert phase in ("opening_15m", "morning")


def test_storage_roundtrip_normalizes_to_ist(tmp_path, monkeypatch):
    test_db = tmp_path / "ist_test.db"
    import commentary.storage.db as comm_db
    monkeypatch.setattr(comm_db, "_DB_PATH", test_db)
    monkeypatch.setattr(comm_db, "_RECENT_ENTRIES_CACHE", [])
    monkeypatch.setattr(comm_db, "_CACHE_INITIALIZED", False)
    ensure_commentary_schema()

    # Pass UTC timestamp into entry
    utc_dt = dt.datetime(2026, 10, 13, 3, 56, 4, tzinfo=dt.timezone.utc)
    entry = CommentaryEntry(
        id="entry-utc-1",
        ts=utc_dt,
        phase="morning",
        event_ids=["evt-1"],
        headline="Test IST Headline",
        body="Body",
        verified=True,
    )
    save_commentary_entry(entry, symbol="NIFTY")

    entries = get_recent_commentary_entries("NIFTY", limit=5)
    assert len(entries) >= 1
    found = entries[0]
    # Check that ts stored/returned is ISO with +05:30
    assert "+05:30" in found["ts"]
    assert "09:26:04" in found["ts"]
    comm_db.clear_commentary_cache()
