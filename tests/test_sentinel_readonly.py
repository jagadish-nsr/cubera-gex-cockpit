"""Pillar A5 — EXTERNAL serve must not write data/sentinel.db."""

from __future__ import annotations

import pytest

from sentinel.storage import db as sentinel_db


def test_external_serve_refuses_write_conn(monkeypatch):
    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    monkeypatch.setenv("COCKPIT_ROLE", "serve")
    assert sentinel_db.sentinel_writes_allowed() is False
    with pytest.raises(sentinel_db.SentinelReadOnlyError):
        sentinel_db._get_write_conn()


def test_ingest_role_allows_writes(monkeypatch):
    monkeypatch.delenv("COCKPIT_INGEST_EXTERNAL", raising=False)
    monkeypatch.setenv("COCKPIT_ROLE", "ingest")
    assert sentinel_db.sentinel_writes_allowed() is True


def test_external_ensure_historical_is_list_only(monkeypatch):
    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    # Must not raise — read path only.
    rows = sentinel_db.ensure_historical_daily_cvd("NIFTY-FUT", symbol="NIFTY", days=3)
    assert isinstance(rows, list)


def test_owns_sentinel_wal_false_on_external(monkeypatch):
    from app.admin.health_daemon import owns_sentinel_wal

    monkeypatch.setenv("COCKPIT_INGEST_EXTERNAL", "1")
    assert owns_sentinel_wal() is False
    monkeypatch.delenv("COCKPIT_INGEST_EXTERNAL", raising=False)
    monkeypatch.setenv("COCKPIT_ROLE", "ingest")
    assert owns_sentinel_wal() is True
