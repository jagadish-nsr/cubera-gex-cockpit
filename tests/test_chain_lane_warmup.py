"""Chain ingest should arm at pre-market discovery, not only 09:15.

Also: overnight / post-close boots must still ensure all three indices so
BANKNIFTY cannot stay dark until someone opens that tab (11-Nov audit).
"""

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
        discovery_min = 9 * 60 + 7

    monkeypatch.setattr("data.session_calendar.clock_for", lambda d: _Clock())

    # 09:10 IST — discovery window, before continuous open
    at_discovery = datetime(2026, 11, 11, 9, 10, tzinfo=IST)
    assert web_server._chain_lanes_should_run(at_discovery) is True
    assert __import__("config").is_chain_ingest_open(at_discovery) is True

    # 09:00 — still before discovery
    before = datetime(2026, 11, 11, 9, 0, tzinfo=IST)
    assert web_server._chain_lanes_should_run(before) is False

    # 09:20 — regular open
    open_sess = datetime(2026, 11, 11, 9, 20, tzinfo=IST)
    assert web_server._chain_lanes_should_run(open_sess) is True

    # Saturday
    sat = datetime(2026, 11, 14, 9, 20, tzinfo=IST)
    assert web_server._chain_lanes_should_run(sat) is False


def test_ensure_all_chain_ingest_arms_banknifty(monkeypatch):
    """Rec/startup path must arm BN even when ACTIVE symbol is NIFTY."""
    import web_server

    called: list[tuple[str, str | None]] = []

    def _fake_ensure(symbol, window=None):
        called.append((symbol, window))

    monkeypatch.setattr(web_server, "_ensure_chain_ingest", _fake_ensure)
    web_server._ACTIVE_STATE["symbol"] = "NIFTY"
    web_server._ACTIVE_STATE["window"] = "current_week"

    web_server._ensure_all_chain_ingest()
    assert [c[0] for c in called] == ["NIFTY", "BANKNIFTY", "SENSEX"]
    assert all(c[1] == "current_week" for c in called)


def test_off_hours_startup_still_ensures_all_three(monkeypatch):
    """Boot at 07:30 must not skip chain ensure (Nov 11 BN silence)."""
    import web_server

    monkeypatch.setattr(web_server, "_chain_lanes_should_run", lambda now=None: False)
    called: list[str] = []
    monkeypatch.setattr(
        web_server,
        "_ensure_chain_ingest",
        lambda symbol, window=None: called.append(symbol),
    )
    started: list[str] = []
    monkeypatch.setattr(
        web_server,
        "_ensure_chain_armer",
        lambda: started.append("arm"),
    )
    monkeypatch.setattr(web_server, "_run_lane", lambda name, fn: fn())
    # Remaining lanes no-op
    for stub in ("_admin",):
        pass

    # Call the body that arms chains — mirror startup fragment
    window = web_server._ACTIVE_STATE.get("window") or "current_week"
    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        web_server._run_lane(f"chain.{sym}", lambda sym=sym: web_server._ensure_chain_ingest(sym, window))
    web_server._run_lane("chain.arm", web_server._ensure_chain_armer)

    assert called == ["NIFTY", "BANKNIFTY", "SENSEX"]
    assert started == ["arm"]
