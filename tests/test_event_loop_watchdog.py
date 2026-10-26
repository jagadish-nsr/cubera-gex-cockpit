"""The watchdog fires only after the event loop has actually stopped ticking."""

from app.event_loop_watchdog import stalled


def test_fresh_heartbeat_is_not_a_stall():
    assert stalled(now=100.0, beat=90.0, armed_at=0.0, stall_s=45.0) is False


def test_stale_heartbeat_is_a_stall():
    assert stalled(now=100.0, beat=50.0, armed_at=0.0, stall_s=45.0) is True


def test_loop_that_never_ticks_stalls_from_arm_time():
    assert stalled(now=50.0, beat=0.0, armed_at=0.0, stall_s=45.0) is True
    assert stalled(now=30.0, beat=0.0, armed_at=0.0, stall_s=45.0) is False


def test_non_positive_threshold_never_fires():
    assert stalled(now=100.0, beat=0.0, armed_at=0.0, stall_s=0.0) is False
