"""Chain ingest should arm at pre-market discovery, not only 09:15."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def test_chain_lanes_run_from_discovery_not_only_open(monkeypatch):
    import web_server

    monkeypatch.setattr("data.holidays.is_holiday", lambda d: False)
    monkeypatch.setattr(
        "data.session_calendar.is_muhurat_session",
        lambda d: False,
    )

    class _Clock:
        open_min = 9 * 60 + 15
        fno_close_min = 15 * 60 + 40

    monkeypatch.setattr("data.session_calendar.clock_for", lambda d: _Clock())

    # 09:10 IST — discovery window, before continuous open
    at_discovery = datetime(2026, 11, 11, 9, 10, tzinfo=IST)
    assert web_server._chain_lanes_should_run(at_discovery) is True

    # 09:00 — still before discovery
    before = datetime(2026, 11, 11, 9, 0, tzinfo=IST)
    assert web_server._chain_lanes_should_run(before) is False

    # 09:20 — regular open
    open_sess = datetime(2026, 11, 11, 9, 20, tzinfo=IST)
    assert web_server._chain_lanes_should_run(open_sess) is True

    # Saturday
    sat = datetime(2026, 11, 14, 9, 20, tzinfo=IST)
    assert web_server._chain_lanes_should_run(sat) is False
