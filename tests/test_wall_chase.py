"""Wall-chase criteria — 2-week sample (2026-11-06 → 19) patterns."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from analytics.wall_chase import (
    CHASE_TAPE_MIN_SCORE,
    compute_chase_stretch,
    evaluate_wall_chase,
    parse_strikes_from_headline,
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


def test_call_wall_chase_with_bearish_tape_does_not_flag_chase_active():
    """23-Nov: CALL wall migrations + PUT WATCH must not set wall_chase for notify."""
    now = datetime(2026, 11, 23, 15, 20, tzinfo=IST).timestamp()
    alert = evaluate_market_alert(
        {
            "spot": 25366,
            "strike_step": 50,
            "is_market_open": True,
            "as_of_time": "15:20:00 IST",
            "kpis": {
                "call_wall": {"val": 25400},
                "put_wall": {"val": 25350},
                "gamma_flip": {"val": 25370},
                "regime": {"val": "NEGATIVE"},
            },
            "orderflow_alerts": [
                {
                    "type": "CONTINUATION_BEAR",
                    "category": "BEARISH",
                    "score": 80,
                    "status": "NEW",
                    "ts": now - 20,
                    "insight_text": "Seller continuation.",
                    "invalidation_level": 25415,
                }
            ],
            "wall_events": [
                {
                    "kind": "WALL_MIGRATION",
                    "side": "CALL",
                    "headline": "Call Wall Migrated from ₹25,450 to ₹25,400",
                    "ts": now - 900,
                },
                {
                    "kind": "WALL_BREAK",
                    "side": "CALL",
                    "headline": "Call Wall Broken at ₹25,400",
                    "ts": now - 400,
                },
            ],
        },
        now_ts=now,
    )
    assert alert["stance"] == "PUT WATCH"
    assert alert["wall_chase_side"] == "CALL"
    assert alert["wall_chase"] is False
    assert "WALL CHASE" not in str(alert.get("badge") or "")


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
    assert evs[0]["strike"] == 82700.0
    assert evs[1]["old_strike"] == 82700.0
    assert evs[1]["new_strike"] == 82800.0


def test_parse_strikes_and_chase_stretch_sensex_shape():
    assert parse_strikes_from_headline("Call Wall Broken at ₹82,600") == [82600.0]
    assert parse_strikes_from_headline("Call Wall Migrated from ₹82,500 to ₹82,600") == [82500.0, 82600.0]
    events = [
        {"side": "CALL", "headline": "Call Wall Migrated from ₹82,500 to ₹82,600"},
        {"side": "CALL", "headline": "Call Wall Broken at ₹82,600"},
        {"side": "CALL", "headline": "Call Wall Migrated from ₹82,600 to ₹82,700"},
    ]
    stretch = compute_chase_stretch(
        side="CALL",
        spot=82753,
        call_wall=82700,
        put_wall=82500,
        strike_step=100,
        through_pts=53,
        wall_events=events,
        magnet=82600,
        flip=82600,
    )
    assert stretch["first_wall"] == 82500.0
    assert stretch["last_wall"] == 82700.0
    assert stretch["wall_span_pts"] == 200.0
    assert stretch["run_pts"] == 253.0  # 82753 - 82500
    assert stretch["next_map_strike"] == 82800.0
    assert stretch["stretch_to_next_pts"] == 47.0  # 82800 - 82753
    assert stretch["potential_stretch_pts"] == 353.0  # run + one 100-pt step
    assert "short_call_short_put" in stretch["note"]


def test_middle_zone_tape_alert_uses_single_invalidation_level():
    """25-Nov: when tape has invalidation_level, trigger must not name a second wall level."""
    now = datetime(2026, 11, 25, 14, 1, tzinfo=IST).timestamp()
    alert = evaluate_market_alert(
        {
            "spot": 83897.34,
            "strike_step": 100,
            "is_market_open": True,
            "as_of_time": "14:01:00 IST",
            "kpis": {
                "call_wall": {"val": 85000},
                "put_wall": {"val": 83500},
                "gamma_flip": {"val": 83700},
                "regime": {"val": "POSITIVE"},
            },
            "orderflow_alerts": [
                {
                    "type": "ABSORPTION_BEAR",
                    "category": "BEARISH",
                    "score": 95,
                    "status": "NEW",
                    "ts": now - 15,
                    "insight_text": "Passive absorption at resistance.",
                    "invalidation_level": 83985,
                }
            ],
        },
        now_ts=now,
    )
    assert alert["zone"] == "PINNED"
    assert alert["stance"] == "PUT WATCH"
    assert "83,985" in alert["kill"]
    assert "83,500" not in alert["trigger"]

