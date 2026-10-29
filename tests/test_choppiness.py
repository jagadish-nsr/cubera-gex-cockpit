"""Hand checks for the path-shape meter. No gamma sign enters the number."""
from datetime import datetime, timedelta, timezone

from analytics.choppiness import (
    CHOP_BARS,
    compute_choppiness_meter,
    dreiss_chop,
    path_efficiency,
    resample_session_5m,
    vwap_crosses,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _flat_chop_bars():
    """15 bars pinned in a 10-point box. Sum of true range 140, span 10 → chop 100."""
    bars = []
    for _ in range(CHOP_BARS + 1):
        bars.append({"high": 10.0, "low": 0.0, "close": 5.0})
    return bars


def _trend_chop_bars():
    """15 bars stepping 10 points. Sum of true range equals the span → chop 0."""
    bars = []
    for i in range(CHOP_BARS + 1):
        low = 10.0 * i
        bars.append({"high": low + 10.0, "low": low, "close": low + 10.0})
    return bars


def test_dreiss_chop_is_100_when_the_path_fills_a_box():
    chop = dreiss_chop(_flat_chop_bars())
    assert chop == 100.0


def test_dreiss_chop_is_0_when_each_step_extends_the_range():
    chop = dreiss_chop(_trend_chop_bars())
    assert chop == 0.0


def test_dreiss_chop_stays_quiet_below_15_bars():
    assert dreiss_chop(_flat_chop_bars()[:10]) is None


def test_path_efficiency_is_one_on_a_straight_push():
    bars = [{"close": 100.0 + i, "high": 101.0 + i, "low": 100.0 + i} for i in range(30)]
    efficiency, n = path_efficiency(bars)
    assert n == 30
    assert efficiency == 1.0


def test_path_efficiency_is_zero_when_the_close_round_trips():
    closes = []
    for i in range(15):
        closes.append(100.0 if i % 2 == 0 else 101.0)
    bars = [{"close": c, "high": c, "low": c} for c in closes]
    efficiency, n = path_efficiency(bars)
    assert n == 15
    assert efficiency == 0.0


def test_vwap_crosses_count_side_changes():
    bars = []
    for i in range(15):
        close = 101.0 if i % 2 == 0 else 99.0
        bars.append({"close": close, "high": close, "low": close, "vwap": 100.0})
    assert vwap_crosses(bars) == 14


def test_meter_calls_a_boxed_path_choppy_and_a_push_directional():
    boxed = []
    for i in range(30):
        close = 100.0 if i % 2 == 0 else 101.0
        boxed.append({"close": close, "high": 110.0, "low": 100.0, "vwap": 100.5})
    choppy = compute_choppiness_meter(boxed, bars_5m=_flat_chop_bars())
    # 30 alternating closes: net 1 point, path 29 points → 1/29 ≈ 0.03.
    assert choppy["state"] == "choppy"
    assert choppy["chop"] == 100.0
    assert choppy["efficiency"] == 0.03
    assert "boxed" in choppy["line"]

    push = [{"close": 100.0 + i, "high": 101.0 + i, "low": 100.0 + i, "vwap": 100.0 + i} for i in range(30)]
    directional = compute_choppiness_meter(push, bars_5m=_trend_chop_bars())
    assert directional["state"] == "directional"
    assert directional["chop"] == 0.0
    assert directional["efficiency"] == 1.0


def test_meter_is_mixed_when_the_windows_disagree_and_quiet_when_empty():
    one = [{"close": 100.0 + i, "high": 101.0 + i, "low": 100.0 + i, "vwap": 100.0} for i in range(30)]
    mixed = compute_choppiness_meter(one, bars_5m=_flat_chop_bars())
    assert mixed["state"] == "mixed"
    assert compute_choppiness_meter([])["state"] == "not_ready"


def test_five_minute_buckets_follow_the_cash_open_and_drop_preopen():
    day = datetime(2026, 10, 29, tzinfo=IST)
    stamps = [
        day.replace(hour=9, minute=0),
        day.replace(hour=9, minute=15),
        day.replace(hour=9, minute=16),
        day.replace(hour=9, minute=19),
        day.replace(hour=9, minute=20),
    ]
    bars = []
    for i, stamp in enumerate(stamps):
        px = 100.0 + i
        bars.append({"start_ts": stamp, "open": px, "high": px + 1, "low": px - 1, "close": px})
    five = resample_session_5m(bars)
    assert len(five) == 2
    assert five[0]["start_ts"].minute == 15
    assert five[1]["start_ts"].minute == 20
    assert five[0]["high"] == 104.0
    assert five[0]["low"] == 100.0
