"""Map-lag / open-thrust — quiet detector (no WATCH, no notify)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from analytics.map_lag_run import (
    MOVE_THR_PTS,
    evaluate_map_lag_run,
    measure_spot_run,
    move_threshold,
)
from analytics.market_alert import evaluate_market_alert

IST = timezone(timedelta(hours=5, minutes=30))


def _path(start_spot: float, end_spot: float, *, t1: float, minutes: int = 30, n: int = 8):
    step = (end_spot - start_spot) / max(n - 1, 1)
    t0 = t1 - minutes * 60
    out = []
    for i in range(n):
        out.append([t0 + i * (minutes * 60) / max(n - 1, 1), start_spot + step * i])
    return out


def test_thresholds_tighter_than_discovery_scan_and_morning_bias():
    assert MOVE_THR_PTS["SENSEX"] >= 400
    assert MOVE_THR_PTS["NIFTY"] >= 120
    morning = 9 * 60 + 45
    afternoon = 13 * 60 + 40
    assert move_threshold("SENSEX", mins_of_day=morning) == 400
    assert move_threshold("SENSEX", mins_of_day=afternoon) == 600.0


def test_sensex_morning_open_thrust_is_map_lag_not_watch():
    """Sensex 19 Nov 09:30–10:00 shape: +520, put-side walls, soft CONT, absorb bear."""
    now = datetime(2026, 11, 19, 10, 0, tzinfo=IST).timestamp()
    path = _path(81738, 82258, t1=now, minutes=30)
    lag = evaluate_map_lag_run(
        symbol="SENSEX",
        spot_path=path,
        zone="PINNED",
        wall_events=[
            {
                "kind": "WALL_MIGRATION",
                "side": "PUT",
                "headline": "Put Wall Migrated from ₹82,000 to ₹81,500",
                "ts": now - 1800,
            },
            {
                "kind": "WALL_MIGRATION",
                "side": "PUT",
                "headline": "Put Wall Migrated from ₹81,500 to ₹82,000",
                "ts": now - 1200,
            },
        ],
        orderflow_alerts=[
            {
                "type": "CONTINUATION_BULL",
                "category": "BULLISH",
                "score": 45,
                "status": "NEW",
                "ts": now - 60,
            },
            {
                "type": "ABSORPTION_BEAR",
                "category": "BEARISH",
                "score": 100,
                "status": "NEW",
                "ts": now - 30,
            },
        ],
        now_ts=now,
    )
    assert lag["active"] is True
    assert lag["side"] == "CALL"
    assert lag["abs_move_pts"] >= 400
    assert lag["reason_n"] >= 2
    assert any("OPP_SIDE" in r or "LT2" in r for r in lag["reasons"])


def test_small_move_stays_quiet():
    now = datetime(2026, 11, 19, 10, 0, tzinfo=IST).timestamp()
    path = _path(82000, 82150, t1=now)  # +150 < 400 SX thr
    lag = evaluate_map_lag_run(
        symbol="SENSEX",
        spot_path=path,
        zone="PINNED",
        wall_events=[],
        orderflow_alerts=[],
        now_ts=now,
    )
    assert lag["active"] is False


def test_strong_wall_chase_suppresses_map_lag():
    now = datetime(2026, 11, 19, 14, 0, tzinfo=IST).timestamp()
    # Afternoon thr for SX = 600; use a huge move that would otherwise qualify
    path = _path(82000, 82700, t1=now, minutes=30)
    lag = evaluate_map_lag_run(
        symbol="SENSEX",
        spot_path=path,
        zone="ABOVE_CALL_WALL",
        wall_events=[
            {"kind": "WALL_BREAK", "side": "CALL", "headline": "Call Wall Broken at ₹82,500", "ts": now - 900},
            {"kind": "WALL_MIGRATION", "side": "CALL", "headline": "Call Wall Migrated from ₹82,500 to ₹82,600", "ts": now - 300},
        ],
        orderflow_alerts=[
            {
                "type": "CONTINUATION_BULL",
                "category": "BULLISH",
                "score": 69,
                "status": "NEW",
                "ts": now - 20,
            }
        ],
        now_ts=now,
    )
    assert lag["active"] is False
    assert "Suppressed" in (lag.get("note") or "")


def test_needs_two_fail_reasons():
    now = datetime(2026, 11, 19, 10, 0, tzinfo=IST).timestamp()
    path = _path(25000, 25150, t1=now)  # Nifty +150 ≥ 120
    # Chase zone + agreeing CONT 70 + no walls → only LT2 reason → quiet
    lag = evaluate_map_lag_run(
        symbol="NIFTY",
        spot_path=path,
        zone="ABOVE_CALL_WALL",
        wall_events=[],
        orderflow_alerts=[
            {
                "type": "CONTINUATION_BULL",
                "category": "BULLISH",
                "score": 70,
                "status": "NEW",
                "ts": now - 15,
            }
        ],
        now_ts=now,
    )
    assert lag["active"] is False


def test_alert_tape_map_lag_stays_stay_out_no_scenario_hijack():
    now = datetime(2026, 11, 19, 10, 0, tzinfo=IST).timestamp()
    alert = evaluate_market_alert(
        {
            "symbol": "SENSEX",
            "spot": 82258,
            "strike_step": 100,
            "is_market_open": True,
            "as_of_time": "10:00:00 IST",
            "spot_path": _path(81738, 82258, t1=now),
            "kpis": {
                "call_wall": {"val": 82500},
                "put_wall": {"val": 82000},
                "gamma_flip": {"val": 82200},
                "regime": {"val": "POSITIVE"},
            },
            "orderflow_alerts": [
                {
                    "type": "CONTINUATION_BULL",
                    "category": "BULLISH",
                    "score": 45,
                    "status": "NEW",
                    "ts": now - 40,
                },
                {
                    "type": "ABSORPTION_BEAR",
                    "category": "BEARISH",
                    "score": 100,
                    "status": "NEW",
                    "ts": now - 20,
                },
            ],
            "wall_events": [
                {
                    "kind": "WALL_MIGRATION",
                    "side": "PUT",
                    "headline": "Put Wall Migrated from ₹81,500 to ₹82,000",
                    "ts": now - 1000,
                },
                {
                    "kind": "WALL_MIGRATION",
                    "side": "PUT",
                    "headline": "Put Wall Migrated from ₹82,000 to ₹81,500",
                    "ts": now - 1600,
                },
            ],
            "market_breadth": {},
            "gamma_blast": {},
        },
        now_ts=now,
    )
    assert alert is not None
    assert alert["map_lag"] is True
    assert alert["stance"] == "STAY OUT"
    assert "MAP LAG" in alert["badge"]
    assert "Informational only" in alert["drivers"]
    # Must not invent a MAP_LAG scenario (would ring brass bell on flap)
    assert alert["scenario"] != "MAP_LAG_RUN"
    assert alert.get("wall_chase") is True  # put-side chase active in feed
    assert alert.get("wall_chase_side") == "PUT"


def test_measure_spot_run_signed():
    now = 1_700_000_000.0
    path = _path(100.0, 250.0, t1=now, minutes=20)
    run = measure_spot_run(path, now_ts=now)
    assert run["side"] == "CALL"
    assert run["abs_move_pts"] == 150.0
