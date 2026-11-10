"""Hand checks for 15m GO signal digest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from analytics.go_digest import build_signal_digest

_IST = timezone(timedelta(hours=5, minutes=30))


def test_digest_rolls_go_sentinel_commentary():
    now = datetime(2026, 11, 10, 14, 0, tzinfo=_IST)
    go_events = [
        {
            "event_type": "EMIT",
            "state": "FIRE",
            "strategy_key": "CONTINUATION_BULL",
            "ts": (now - timedelta(minutes=5)).isoformat(),
        },
        {
            "event_type": "EMIT",
            "state": "SCOUT",
            "strategy_key": "IRON_CONDOR",
            "ts": (now - timedelta(minutes=8)).isoformat(),
        },
        {
            "event_type": "STAGE",
            "state": "FIRE",
            "ts": (now - timedelta(minutes=4)).isoformat(),
        },
        # Outside window
        {
            "event_type": "EMIT",
            "state": "FIRE",
            "strategy_key": "OLD",
            "ts": (now - timedelta(minutes=40)).isoformat(),
        },
    ]
    signals = [
        {"bias": "LONG", "type": "ABSORPTION", "ts": (now - timedelta(minutes=2)).timestamp()},
        {"bias": "SHORT", "type": "SWEEP", "ts": (now - timedelta(minutes=3)).timestamp()},
        {"bias": "LONG", "type": "IMBALANCE", "ts": (now - timedelta(minutes=1)).timestamp()},
    ]
    commentary = [
        {"headline": "Put wall holding location", "ts": (now - timedelta(minutes=6)).isoformat()},
    ]
    d = build_signal_digest(
        go_events=go_events,
        sentinel_signals=signals,
        commentary=commentary,
        now=now,
        window_minutes=15,
    )
    assert d["counts"]["go_fire"] == 1
    assert d["counts"]["go_scout"] == 1
    assert d["counts"]["go_stage"] == 1
    assert d["counts"]["sentinel_bull"] == 2
    assert d["counts"]["sentinel_bear"] == 1
    assert d["counts"]["commentary"] == 1
    assert "tape lean LONG" in d["lean"]
    assert "1F" in d["summary"] or "GO 1F" in d["summary"]
    assert d["bites"]


def test_digest_quiet_when_empty():
    now = datetime(2026, 11, 10, 10, 0, tzinfo=_IST)
    d = build_signal_digest(now=now, window_minutes=15)
    assert d["counts"]["go_fire"] == 0
    assert "Quiet" in d["summary"]
