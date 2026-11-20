"""Wall-chase criteria — 2-week sample (2026-11-06 → 19) patterns."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from analytics.wall_chase import (
    CHASE_TAPE_MIN_SCORE,
    evaluate_wall_chase,
    should_promote_extension_watch,
    tape_score_floor_for_zone,
    wall_events_from_commentary,
)
from analytics.market_alert import evaluate_market_alert, pick_fresh_signal

IST = timezone(timedelta(hours=5, minutes=30))


def test_chase_tape_floor_softens_continuation_in_extension_zone():
    assert tape_score_floor_for_zone("ABOVE_CALL_WALL", "CONTINUATION_BULL") == CHASE_TAPE_MIN_SCORE
    assert tape_score_floor_for_zone("TEST_CALL", "CVD_DIVERGENCE_BULL") == 65
    assert tape_score_floor_for_zone("ABOVE_CALL_WALL", "ABSORPTION_BULL") == 70
    assert tape_score_floor_for_zone("PINNED", "CONTINUATION_BULL") == 70


def test_pick_fresh_signal_accepts_cont_bull_69_on_above_call():
    now = datetime(2026, 11, 19, 14, 0, tzinfo=IST).timestamp()
    alerts = [
        {
            "type": "CONTINUATION_BULL",
            "category": "BULLISH",
            "score": 69,
            "status": "NEW",
            "ts": now - 15,
            "insight_text": "Buyer continuation.",
        }
    ]
    assert pick_fresh_signal(alerts, now) is None  # hard floor without zone
    picked = pick_fresh_signal(alerts, now, zone="ABOVE_CALL_WALL")
    assert picked is not None
    assert picked["score"] == 69
    assert picked["_score_floor"] == 65


def test_above_call_with_cont_bull_69_is_call_watch():
    """Sensex 19 Nov 14:00 shape: through call wall + CONT 69 → CALL WATCH."""
    now = datetime(2026, 11, 19, 14, 0, tzinfo=IST).timestamp()
    alert = evaluate_market_alert(
        {
            "spot": 82753,
            "strike_step": 100,
            "is_market_open": True,
            "as_of_time": "14:00:00 IST",
            "kpis": {
                "call_wall": {"val": 82600},
                "put_wall": {"val": 82500},
                "gamma_flip": {"val": 82600},
                "regime": {"val": "POSITIVE"},
            },
            "market_breadth": {"advances": 12, "declines": 2},
            "orderflow_alerts": [
                {
                    "type": "CONTINUATION_BULL",
                    "category": "BULLISH",
                    "score": 69,
                    "status": "NEW",
                    "ts": now - 20,
                    "insight_text": "Strong buyer continuation.",
                    "invalidation_level": 82550,
                }
            ],
            "wall_events": [
                {
                    "kind": "WALL_MIGRATION",
                    "side": "CALL",
                    "headline": "Call Wall Migrated from ₹82,500 to ₹82,600",
                    "ts": now - 1200,
                },
                {
                    "kind": "WALL_BREAK",
                    "side": "CALL",
                    "headline": "Call Wall Broken at ₹82,600",
                    "ts": now - 600,
                },
            ],
        },
        now_ts=now,
    )
    assert alert["zone"] == "ABOVE_CALL_WALL"
    assert alert["stance"] == "CALL WATCH"
    assert alert["wall_chase"] is True
    assert alert["wall_chase_side"] == "CALL"
    assert "WALL CHASE" in alert["badge"]
    assert "CONTINUATION BULL" in alert["badge"].upper() or "CONTINUATION_BULL" in alert["scenario"]
    assert "Stop beyond the wall" in alert["risk"] or "broken call wall" in alert["trigger"].lower()


def test_evaluate_wall_chase_needs_two_same_side():
    now = 1_700_000_000.0
    one = evaluate_wall_chase(
        [{"kind": "WALL_BREAK", "side": "CALL", "headline": "Call Wall Broken", "ts": now - 60}],
        now_ts=now,
    )
    assert one["active"] is False
    two = evaluate_wall_chase(
        [
            {"kind": "WALL_BREAK", "side": "CALL", "headline": "Call Wall Broken", "ts": now - 600},
            {"kind": "WALL_MIGRATION", "side": "CALL", "headline": "Call Wall Migrated", "ts": now - 120},
        ],
        now_ts=now,
    )
    assert two["active"] is True
    assert two["side"] == "CALL"
    assert two["n_events"] == 2


def test_should_promote_extension_watch():
    assert should_promote_extension_watch(zone="ABOVE_MAGNET", sig_side="CALL") == "CALL WATCH"
    assert should_promote_extension_watch(zone="BELOW_PUT_WALL", sig_side="PUT") == "PUT WATCH"
    assert should_promote_extension_watch(zone="ABOVE_CALL_WALL", sig_side="") == ""
    assert should_promote_extension_watch(zone="PINNED", sig_side="CALL") == ""


def test_wall_events_from_commentary_tags():
    entries = [
        {
            "ts": "2026-11-19T14:15:20+05:30",
            "headline": "Call Wall Broken at ₹82,700",
            "tags": ["WALL_BREAK"],
        },
        {
            "ts": "2026-11-19T14:25:20+05:30",
            "headline": "Call Wall Migrated from ₹82,700 to ₹82,800",
            "tags_json": '["WALL_MIGRATION"]',
        },
        {
            "ts": "2026-11-19T14:30:00+05:30",
            "headline": "IV Crush",
            "tags": ["IV_CRUSH"],
        },
    ]
    evs = wall_events_from_commentary(entries)
    assert len(evs) == 2
    assert evs[0]["side"] == "CALL"
    assert evs[0]["kind"] == "WALL_BREAK"
