"""Geometry-first marquee tape: walls, Sentinel overlay, no sector-trend trigger."""

from datetime import datetime, timezone, timedelta

from analytics.market_alert import classify_zone, evaluate_market_alert, pick_fresh_signal

IST = timezone(timedelta(hours=5, minutes=30))


def test_classify_below_magnet_when_walls_coincide():
    geo = classify_zone(spot=22428, call_wall=22500, put_wall=22500, step=50)
    assert geo["zone"] == "BELOW_MAGNET"
    assert geo["through_pts"] == 72


def test_classify_pinned_only_inside_distinct_corridor():
    assert classify_zone(22450, 22700, 22300, 50)["zone"] == "PINNED"
    assert classify_zone(22428, 22500, 22500, 50)["zone"] != "PINNED"
    assert classify_zone(22680, 22700, 22300, 50)["zone"] == "TEST_CALL"
    assert classify_zone(22800, 22700, 22300, 50)["zone"] == "ABOVE_CALL_WALL"


def test_pick_fresh_signal_ignores_stale_and_failed():
    now = datetime(2026, 10, 23, 12, 39, tzinfo=IST).timestamp()
    alerts = [
        {"type": "CONTINUATION_BEAR", "score": 90, "status": "FAILED", "ts": now - 30},
        {"type": "CVD_DIVERGENCE_BULL", "score": 68, "status": "NEW", "ts": now - 20},
        {"type": "CONTINUATION_BEAR", "score": 80, "status": "NEW", "ts": now - 400},
        {"type": "ABSORPTION_BULL", "score": 74, "status": "NEW", "ts": now - 40},
    ]
    picked = pick_fresh_signal(alerts, now)
    assert picked["type"] == "ABSORPTION_BULL"


def test_heavy_sector_red_does_not_print_downtrend_when_below_magnet():
    now = datetime(2026, 10, 23, 12, 39, tzinfo=IST).timestamp()
    alert = evaluate_market_alert(
        {
            "spot": 22428,
            "strike_step": 50,
            "is_market_open": True,
            "market_state": "OPEN",
            "is_pre_bell_ephemeral": False,
            "as_of_time": "12:39:02 IST",
            "kpis": {
                "call_wall": {"val": 22500},
                "put_wall": {"val": 22500},
                "gamma_flip": {"val": 22386},
                "regime": {"val": "POSITIVE"},
            },
            "gamma_blast": {"score": 28, "is_expiry": False},
            "market_breadth": {"advances": 1, "declines": 13, "ratio": 0.08},
            "orderflow_alerts": [],
        },
        now_ts=now,
    )
    assert alert["scenario"] == "BELOW_SUPPORT"
    assert alert["meaning"] == "Spot is through the only gamma cluster. It is not a pin."
    assert "DOWNTREND" not in alert["badge"]
    assert "institutional selling" not in alert["body"].lower()
    assert "prior close" in alert["diagnostics"]


def test_fresh_sentinel_overlays_geometry():
    now = datetime(2026, 10, 23, 12, 39, tzinfo=IST).timestamp()
    alert = evaluate_market_alert(
        {
            "spot": 22450,
            "strike_step": 50,
            "is_market_open": True,
            "market_state": "OPEN",
            "as_of_time": "12:39:02 IST",
            "kpis": {
                "call_wall": {"val": 22700},
                "put_wall": {"val": 22300},
                "gamma_flip": {"val": 22400},
                "regime": {"val": "POSITIVE"},
            },
            "gamma_blast": {"score": 20, "is_expiry": False},
            "market_breadth": {"advances": 4, "declines": 10, "ratio": 0.4},
            "orderflow_alerts": [
                {
                    "type": "CONTINUATION_BEAR",
                    "category": "BEARISH",
                    "score": 82,
                    "status": "NEW",
                    "ts": now - 25,
                    "insight_text": "Seller sweep 2400 units.",
                    "actionable_play": "Stand aside until delta cools.",
                    "invalidation_level": 22520,
                }
            ],
        },
        now_ts=now,
    )
    assert alert["scenario"] == "TAPE_CONTINUATION_BEAR"
    assert alert["zone"] == "PINNED"
    assert alert["headline"].startswith("CONTINUATION BEAR")
    assert alert["meaning"] == "Spot is inside the corridor. The corridor is a map, not a fade."
    assert "Seller sweep" in alert["body"]
    assert "22,520" in alert["action"]
    assert "STRONG DOWNTREND" not in alert["headline"]


def test_morning_briefing_names_prior_session_book():
    alert = evaluate_market_alert(
        {
            "spot": 22600,
            "strike_step": 50,
            "session_phase": "PRE_MARKET_SETUP",
            "is_morning_briefing": True,
            "is_market_open": False,
            "market_state": "CLOSED",
            "is_pre_bell_ephemeral": False,
            "snapshot_as_of": "15:35:00 IST",
            "kpis": {
                "call_wall": {"val": 22800},
                "put_wall": {"val": 22400},
                "gamma_flip": {"val": 22550},
                "regime": {"val": "NEGATIVE"},
            },
            "pre_market": {
                "status_text": "Previous NSE close ₹22,600 sits 50 pts above the flip ₹22,550.",
                "oi_as_of": "2026-10-12T15:35:00+05:30",
                "fii_bias": "NEUTRAL",
                "prior_session_cvd": 12,
                "is_discovered": False,
                "discovered_spot": None,
            },
        }
    )
    assert alert["scenario"] == "MORNING_BRIEFING"
    assert alert["meaning"] == "Yesterday's walls and flip, before the cash open."
    assert "22,600" in alert["body"]
    assert "12-10-2026 15:35:00 IST" in alert["diagnostics"]
    assert alert["scenario"] != "PRE_OPEN_SETUP"


def test_pre_open_auction_copy_unchanged_at_0900():
    base = {
        "spot": 22450,
        "strike_step": 50,
        "session_phase": "PRE_MARKET_ENTRY",
        "is_morning_briefing": False,
        "is_pre_bell_ephemeral": True,
        "is_market_open": True,
        "kpis": {
            "call_wall": {"val": 22700},
            "put_wall": {"val": 22300},
            "gamma_flip": {"val": 22400},
            "regime": {"val": "POSITIVE"},
        },
        "gamma_blast": {"score": 10},
        "pre_market": {"is_discovered": False},
        "orderflow_alerts": [{"type": "CONTINUATION_BEAR", "score": 90, "status": "NEW", "ts": 1e18}],
    }
    pre = evaluate_market_alert(base)
    assert pre["scenario"] == "PRE_OPEN_SETUP"
    assert "Cash auction is running" in pre["body"]
    assert "09:08" in pre["body"]


def test_pre_open_and_closed_short_circuit():
    base = {
        "spot": 22450,
        "strike_step": 50,
        "kpis": {
            "call_wall": {"val": 22700},
            "put_wall": {"val": 22300},
            "gamma_flip": {"val": 22400},
            "regime": {"val": "POSITIVE"},
        },
        "gamma_blast": {"score": 10},
        "orderflow_alerts": [{"type": "CONTINUATION_BEAR", "score": 90, "status": "NEW", "ts": 1e18}],
    }
    pre = evaluate_market_alert({**base, "is_pre_bell_ephemeral": True, "is_market_open": True, "pre_market": {}})
    assert pre["scenario"] == "PRE_OPEN_SETUP"
    closed = evaluate_market_alert({**base, "is_market_open": False, "market_state": "CLOSED"})
    assert closed["scenario"] == "MARKET_CLOSED"
    assert closed["meaning"] == "Last published book. Nothing here is a live hedge."


def test_expiry_blast_is_overlay_not_flip_accelerator():
    now = datetime(2026, 10, 23, 14, 10, tzinfo=IST).timestamp()
    alert = evaluate_market_alert(
        {
            "spot": 22410,
            "strike_step": 50,
            "is_market_open": True,
            "as_of_time": "14:10:00 IST",
            "kpis": {
                "call_wall": {"val": 22700},
                "put_wall": {"val": 22300},
                "gamma_flip": {"val": 22420},
                "regime": {"val": "NEGATIVE"},
            },
            "gamma_blast": {"score": 72, "is_expiry": True},
            "market_breadth": {"advances": 7, "declines": 7, "ratio": 1.0},
            "orderflow_alerts": [],
        },
        now_ts=now,
    )
    assert alert["zone"] == "PINNED"
    assert "0DTE BLAST" in alert["badge"]
    assert "parity crossover" in alert["body"]
    assert "BIG MOVE COMING" not in alert["badge"]
    assert "short-gamma hedging" not in alert["body"]
