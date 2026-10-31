"""tests/conftest.py — Global test suite configuration and database isolation."""

import pytest
import data.journal.recommendations_store as rs
import analytics.trade_ideas as ti


@pytest.fixture(autouse=True)
def guard_live_recommendations_db(tmp_path, monkeypatch):
    """Safety guard: Never allow any unit test to touch or mutate data/journal/recommendations.db."""
    test_db = tmp_path / "suite_test_recommendations.db"
    monkeypatch.setattr(rs, "_DB_PATH", test_db)
    with rs._BOOKMARK_CACHE_LOCK:
        rs._BOOKMARK_CACHE.clear()
        rs._CACHE_INITIALIZED = False
    rs.ensure_schema()
    yield test_db
    ti.flush_telemetry(timeout=None)
    with rs._BOOKMARK_CACHE_LOCK:
        rs._BOOKMARK_CACHE.clear()
        rs._CACHE_INITIALIZED = False

