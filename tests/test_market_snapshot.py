"""tests/test_market_snapshot.py — Test suite for Persistent Market Snapshot Registry."""

from datetime import date, datetime, timedelta, timezone

from data.market_snapshot import (
    get_snapshot,
    init_db,
    last_completed_session_date,
    resolve_offmarket_snapshot,
    save_snapshot,
    snapshot_is_older_than,
)

_IST = timezone(timedelta(hours=5, minutes=30))


def test_market_snapshot_persistence(tmp_path, monkeypatch):
    test_db = tmp_path / "test_snapshot.db"
    monkeypatch.setattr("data.market_snapshot.DB_PATH", test_db)

    # Initial state
    init_db()
    assert test_db.exists()

    # Save a valid snapshot
    save_snapshot(
        symbol="NIFTY",
        spot=22721.95,
        prev_close=22603.05,
        open_px=22600.0,
        high=22725.0,
        low=22580.0,
        call_wall=22750.0,
        put_wall=22700.0,
        gamma_flip=22702.07,
        max_pain=22700.0,
        net_gex=1500000.0,
        vix=14.08,
        as_of="2026-10-13 15:30:00",
        source="dhan-verified",
    )

    # Retrieve and verify exact values
    snap = get_snapshot("NIFTY")
    assert snap is not None
    assert snap["symbol"] == "NIFTY"
    assert snap["spot"] == 22721.95
    assert snap["prev_close"] == 22603.05
    assert snap["call_wall"] == 22750.0
    assert snap["put_wall"] == 22700.0
    assert snap["gamma_flip"] == 22702.07
    assert snap["max_pain"] == 22700.0
    assert snap["vix"] == 14.08
    assert snap["source"] == "dhan-verified"


def test_market_snapshot_bootstrap_authentic_values():
    """Verify that get_snapshot bootstraps authentic Dhan data without fabricating fake prices."""
    for sym, expected_spot_range in [
        ("NIFTY", (22000.0, 26000.0)),
        ("BANKNIFTY", (50000.0, 60000.0)),
        ("SENSEX", (70000.0, 80000.0)),
    ]:
        snap = get_snapshot(sym)
        assert snap is not None
        assert snap["spot"] >= expected_spot_range[0]
        assert snap["spot"] <= expected_spot_range[1]
        assert snap["call_wall"] >= snap["put_wall"]
        assert snap["call_wall"] > 0.0
        assert snap["put_wall"] > 0.0
        assert snap["prev_close"] > 0.0


def test_last_completed_session_is_the_previous_trading_day_before_the_close():
    assert last_completed_session_date(datetime(2026, 10, 27, 0, 19, tzinfo=_IST)) == date(2026, 10, 26)
    assert last_completed_session_date(datetime(2026, 10, 26, 10, 0, tzinfo=_IST)) == date(2026, 10, 23)
    assert last_completed_session_date(datetime(2026, 10, 26, 15, 40, tzinfo=_IST)) == date(2026, 10, 26)
    assert last_completed_session_date(datetime(2026, 10, 25, 18, 0, tzinfo=_IST)) == date(2026, 10, 23)


def test_closing_print_keeps_the_stored_previous_close(tmp_path, monkeypatch):
    monkeypatch.setattr("data.market_snapshot.DB_PATH", tmp_path / "snap.db")
    save_snapshot(
        symbol="NIFTY",
        spot=22753.2,
        prev_close=22600.0,
        as_of="2026-10-26T09:00:00+00:00",
        source="dhan-verified",
    )
    save_snapshot(
        symbol="NIFTY",
        spot=22753.2,
        prev_close=22753.2,
        as_of="2026-10-26T10:09:00+00:00",
        source="dhan-verified",
    )
    snap = get_snapshot("NIFTY")
    assert snap["prev_close"] == 22600.0
    assert snap["spot"] == 22753.2


def test_save_snapshot_does_not_invent_walls(tmp_path, monkeypatch):
    monkeypatch.setattr("data.market_snapshot.DB_PATH", tmp_path / "walls.db")
    save_snapshot(
        symbol="NIFTY",
        spot=23000.0,
        as_of="2026-10-29T10:00:00+00:00",
        source="ws-tick",
    )
    row = get_snapshot("NIFTY")
    assert row["spot"] == 23000.0
    assert row["call_wall"] is None
    assert row["put_wall"] is None


def test_october_21_snapshot_is_older_than_the_october_26_book():
    book = datetime(2026, 10, 26, 10, 10, 20, tzinfo=timezone.utc)
    assert snapshot_is_older_than({"as_of": "2026-10-21T04:57:04+00:00"}, book)
    assert not snapshot_is_older_than({"as_of": "2026-10-26T10:10:20+00:00"}, book)


def _save_nifty(spot, prev_close, as_of):
    save_snapshot(
        symbol="NIFTY",
        spot=spot,
        prev_close=prev_close,
        as_of=as_of,
        source="lkgs-store",
    )


def test_stale_local_snapshot_adopts_the_sibling_session(tmp_path, monkeypatch):
    local = tmp_path / "local"
    sibling = tmp_path / "sibling"
    local.mkdir()
    sibling.mkdir()
    monkeypatch.setattr("data.market_snapshot.candidate_journals", lambda: [local, sibling])
    monkeypatch.setattr("data.market_snapshot.DB_PATH", local / "market_snapshot.db")
    _save_nifty(22950.45, 22888.95, "2026-10-21T04:57:04+00:00")
    monkeypatch.setattr("data.market_snapshot.DB_PATH", sibling / "market_snapshot.db")
    _save_nifty(22753.2, 22600.0, "2026-10-26T10:10:20+00:00")
    monkeypatch.setattr("data.market_snapshot.DB_PATH", local / "market_snapshot.db")

    row = resolve_offmarket_snapshot("NIFTY", now=datetime(2026, 10, 27, 0, 19, tzinfo=_IST))
    assert row["spot"] == 22753.2
    assert row["prev_close"] == 22600.0
    assert row["stale_session"] is False
    assert row["adopted_from"] == "sibling"
    assert row["expected_session"] == "2026-10-26"


def test_current_local_snapshot_is_kept(tmp_path, monkeypatch):
    local = tmp_path / "local"
    sibling = tmp_path / "sibling"
    local.mkdir()
    sibling.mkdir()
    monkeypatch.setattr("data.market_snapshot.candidate_journals", lambda: [local, sibling])
    monkeypatch.setattr("data.market_snapshot.DB_PATH", sibling / "market_snapshot.db")
    _save_nifty(22950.45, 22888.95, "2026-10-26T10:20:00+00:00")
    monkeypatch.setattr("data.market_snapshot.DB_PATH", local / "market_snapshot.db")
    _save_nifty(22753.2, 22600.0, "2026-10-26T10:10:20+00:00")

    row = resolve_offmarket_snapshot("NIFTY", now=datetime(2026, 10, 27, 0, 19, tzinfo=_IST))
    assert row["spot"] == 22753.2
    assert row["stale_session"] is False
    assert "adopted_from" not in row


def test_snapshot_older_than_the_last_session_is_marked_stale(tmp_path, monkeypatch):
    local = tmp_path / "local"
    sibling = tmp_path / "sibling"
    local.mkdir()
    sibling.mkdir()
    monkeypatch.setattr("data.market_snapshot.candidate_journals", lambda: [local, sibling])
    monkeypatch.setattr("data.market_snapshot.DB_PATH", local / "market_snapshot.db")
    _save_nifty(22000.0, 21900.0, "2026-10-19T10:00:00+00:00")
    monkeypatch.setattr("data.market_snapshot.DB_PATH", sibling / "market_snapshot.db")
    _save_nifty(22950.45, 22888.95, "2026-10-21T04:57:04+00:00")
    monkeypatch.setattr("data.market_snapshot.DB_PATH", local / "market_snapshot.db")

    row = resolve_offmarket_snapshot("NIFTY", now=datetime(2026, 10, 27, 0, 19, tzinfo=_IST))
    assert row["spot"] == 22950.45
    assert row["stale_session"] is True
    assert row["expected_session"] == "2026-10-26"
    assert row["adopted_from"] == "sibling"


class _Levels:
    def __init__(self, spot):
        self.spot = spot
        self.call_wall = 23000.0
        self.put_wall = 22700.0
        self.gamma_flip = 22800.0
        self.max_pain = 22800.0


class _Dash:
    def __init__(self, spot, fetched_at):
        self.levels = _Levels(spot)
        self.fetched_at = fetched_at
        self.india_vix = None
        self.source_note = None


def test_older_sqlite_row_does_not_replace_a_newer_book(tmp_path, monkeypatch):
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr("data.market_snapshot.candidate_journals", lambda: [local])
    monkeypatch.setattr("data.market_snapshot.DB_PATH", local / "market_snapshot.db")
    monkeypatch.setattr("config.is_market_open", lambda: False)
    _save_nifty(22950.45, 22888.95, "2026-10-21T04:57:04+00:00")

    from web_server import _overlay_sqlite_kpis

    book = _Dash(22753.2, datetime(2026, 10, 26, 10, 10, 20, tzinfo=timezone.utc))
    out = _overlay_sqlite_kpis(book, "NIFTY")
    assert out.levels.spot == 22753.2
    assert out.levels.call_wall == 23000.0


def test_format_ist_stamp_and_offmarket_clock():
    from data.market_snapshot import format_ist_stamp, offmarket_book_clock, session_date_label

    assert format_ist_stamp("2026-10-26T10:10:20.010027+00:00") == "26-10-2026 15:40:20 IST"
    assert format_ist_stamp("2026-10-26 15:40:20") == "26-10-2026 15:40:20 IST"
    assert format_ist_stamp("") == ""
    assert format_ist_stamp(None) == ""
    assert session_date_label("2026-10-26") == "26-10-2026"
    assert session_date_label("not-a-date") == ""

    now = datetime(2026, 10, 27, 6, 39, tzinfo=_IST)
    matched = offmarket_book_clock({"as_of": "2026-10-26T10:10:20+00:00", "spot": 22753.2}, now)
    assert matched["matches_last_session"] is True
    assert matched["stamp"] == "26-10-2026 15:40:20 IST"
    older = offmarket_book_clock({"as_of": "2026-10-21T10:27:04+05:30", "spot": 22600.0}, now)
    assert older["matches_last_session"] is False
    assert older["expected_session"].isoformat() == "2026-10-26"
    blank = offmarket_book_clock({"spot": 22753.2, "as_of": ""}, now)
    assert blank["matches_last_session"] is False
    assert blank["stamp"] == ""
