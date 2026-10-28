"""Alert dedupe. The 60-second loop must refresh one row, not insert a new one."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.admin import db
from app.admin.clock import IST
from app.admin.health_daemon import evaluate_alerts, reset_gap_cache
import data.holidays as holidays


@pytest.fixture(autouse=True)
def offline_holidays(monkeypatch):
    cache: dict[int, set] = {}

    def load(year: int):
        if year not in cache:
            from datetime import date
            found = set()
            names = {}
            for iso, name in holidays.NSE_HOLIDAY_NAMES.items():
                day = date.fromisoformat(iso)
                if day.year == year:
                    found.add(day)
                    names[day] = name
            cache[year] = found
            holidays._calendar_names_cache[year] = names
        return cache[year]

    monkeypatch.setattr(holidays, "_load_or_fetch", load)


@pytest.fixture(autouse=True)
def ledger(tmp_path):
    reset_gap_cache()
    db.set_db_path(tmp_path / "admin_ops.db")
    yield
    db.set_db_path(None)
    reset_gap_cache()


def _token(hours: float, *, expired: bool = False) -> dict:
    return {"present": True, "hours_left": hours, "expired": expired, "source": "file"}


def _feed(*, connected: bool = True, age=1.0) -> dict:
    return {"ws_connected": connected, "last_tick_age_s": age, "is_live": connected and age is not None and age < 10}


def test_token_alert_is_one_row_and_clears(tmp_path):
    now = datetime(2026, 10, 23, 12, 0, tzinfo=IST)
    for _ in range(61):
        result = evaluate_alerts(
            root=tmp_path,
            now=now,
            token_fn=lambda: _token(1.0),
            feed_fn=lambda: _feed(age=1.0),
            include_gaps=False,
        )
    assert result["badge_count"] == 1
    rows = db.list_alerts()
    assert len(rows) == 1
    assert rows[0]["fingerprint"] == "TOKEN|expiry"
    assert rows[0]["severity"] == "CRITICAL"

    cleared = evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=lambda: _feed(age=1.0),
        include_gaps=False,
    )
    assert cleared["badge_count"] == 0
    assert db.list_alerts() == []
    stored = db.list_alerts(include_resolved=True)
    assert stored[0]["resolved_at"]


def test_feed_silence_does_not_alert_outside_the_session(tmp_path):
    silent = lambda: _feed(age=120.0)
    for stamp in (
        datetime(2026, 10, 24, 20, 0, tzinfo=IST),  # Saturday
        datetime(2026, 10, 23, 20, 0, tzinfo=IST),  # Friday after the close
    ):
        evaluate_alerts(
            root=tmp_path,
            now=stamp,
            token_fn=lambda: _token(10.0),
            feed_fn=silent,
            include_gaps=False,
        )
        fingerprints = {row["fingerprint"] for row in db.list_alerts()}
        assert not any(fp.startswith("FEED|silence") for fp in fingerprints)


def test_feed_silence_alerts_during_the_session_and_clears(tmp_path):
    now = datetime(2026, 10, 23, 12, 0, tzinfo=IST)
    evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=lambda: _feed(age=45.0),
        include_gaps=False,
    )
    rows = db.list_alerts()
    assert any(row["fingerprint"] == "FEED|silence" and row["session_date"] == "2026-10-23" and row["severity"] == "CRITICAL" for row in rows)
    evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=lambda: _feed(age=1.0),
        include_gaps=False,
    )
    assert not any(row["fingerprint"].startswith("FEED|silence") for row in db.list_alerts())


def test_gap_alert_skips_sensex_futures_and_clears_when_the_hole_is_filled(tmp_path, monkeypatch):
    from app.admin import health_daemon

    state = {"nifty": "MISSING"}

    def fake_audit(root, symbol, start, end, now=None):
        futures = "MISSING" if symbol == "SENSEX" else ("COMPLETE" if symbol == "BANKNIFTY" else state["nifty"])
        return {"days": [{
            "futures_status": futures,
            "options_matrix_status": "COMPLETE",
            "sentinel_status": "COMPLETE",
        }]}

    monkeypatch.setattr(health_daemon, "audit_range", fake_audit)
    now = datetime(2026, 10, 23, 16, 0, tzinfo=IST)
    evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=lambda: _feed(age=1.0),
    )
    gap = next(row for row in db.list_alerts() if row["fingerprint"] == "GAP|2026-10-23")
    assert "SENSEX futures" not in gap["description"]
    assert "NIFTY futures MISSING" in gap["description"]

    state["nifty"] = "COMPLETE"
    evaluate_alerts(
        root=tmp_path,
        now=now,
        token_fn=lambda: _token(10.0),
        feed_fn=lambda: _feed(age=1.0),
    )
    assert not any(row["fingerprint"].startswith("GAP|") for row in db.list_alerts())
