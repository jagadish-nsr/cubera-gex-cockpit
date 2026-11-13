"""The watchdog fires only after the event loop has actually stopped ticking."""

import os
import sys

from app.event_loop_watchdog import (
    _default_restart,
    _soft_alert,
    main_thread_stack,
    mode_from_env,
    stalled,
)


def test_fresh_heartbeat_is_not_a_stall():
    assert stalled(now=100.0, beat=90.0, armed_at=0.0, stall_s=45.0) is False


def test_stale_heartbeat_is_a_stall():
    assert stalled(now=100.0, beat=50.0, armed_at=0.0, stall_s=45.0) is True


def test_loop_that_never_ticks_stalls_from_arm_time():
    assert stalled(now=50.0, beat=0.0, armed_at=0.0, stall_s=45.0) is True
    assert stalled(now=30.0, beat=0.0, armed_at=0.0, stall_s=45.0) is False


def test_non_positive_threshold_never_fires():
    assert stalled(now=100.0, beat=0.0, armed_at=0.0, stall_s=0.0) is False


def test_main_thread_stack_names_the_caller():
    assert "test_main_thread_stack_names_the_caller" in main_thread_stack()


def test_missing_main_frame_is_reported(monkeypatch):
    monkeypatch.setattr(sys, "_current_frames", lambda: {})
    assert main_thread_stack() == "main thread stack unavailable"


def test_restart_writes_the_stack_before_replacing_the_process(monkeypatch):
    written = []
    monkeypatch.setattr(os, "write", lambda fd, data: written.append((fd, data)) or len(data))
    replaced = []
    monkeypatch.setattr(os, "execv", lambda exe, argv: replaced.append((exe, argv)))
    recorded = []
    monkeypatch.setattr(
        "app.obs.event",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    _default_restart()

    stderr = b"".join(data for fd, data in written if fd == 2)
    assert b"event loop stalled; restarting process\n" in stderr
    assert b"test_restart_writes_the_stack_before_replacing_the_process" in stderr
    assert recorded[0][0] == ("watchdog.restart",)
    assert "test_restart_writes_the_stack_before_replacing_the_process" in recorded[0][1]["stack"]
    assert replaced and replaced[0][0] == sys.executable


def test_soft_alert_logs_without_execv(monkeypatch):
    written = []
    monkeypatch.setattr(os, "write", lambda fd, data: written.append((fd, data)) or len(data))
    replaced = []
    monkeypatch.setattr(os, "execv", lambda exe, argv: replaced.append((exe, argv)))
    recorded = []
    monkeypatch.setattr(
        "app.obs.event",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    _soft_alert()

    stderr = b"".join(data for fd, data in written if fd == 2)
    assert b"soft watchdog (log-only, no restart)" in stderr
    assert recorded[0][0] == ("watchdog.stall",)
    assert recorded[0][1].get("mode") == "soft"
    assert replaced == []


def test_mode_from_env_maps_hard_and_soft(monkeypatch):
    monkeypatch.setenv("COCKPIT_WATCHDOG", "1")
    assert mode_from_env() == "hard"
    monkeypatch.setenv("COCKPIT_WATCHDOG", "soft")
    assert mode_from_env() == "soft"
    monkeypatch.delenv("COCKPIT_WATCHDOG", raising=False)
    assert mode_from_env() is None
