"""tests/conftest.py — Global test suite configuration and database isolation."""

import pytest
import data.journal.recommendations_store as rs
import analytics.trade_ideas as ti
import sentinel.storage.db as sentinel_db


@pytest.fixture(autouse=True)
def disable_live_llm(monkeypatch):
    """Keep unit tests off live Ollama/Gemini unless a test re-enables CUBERA_LLM."""
    monkeypatch.setenv("CUBERA_LLM", "0")


@pytest.fixture(autouse=True)
def guard_live_dhan_http(monkeypatch):
    """No unit test may call the Dhan API. Under pytest the cross-process rate gate is off,
    so a live call here spends prod ingest's /optionchain quota unseen. Tests that need a
    response patch ``requests.post`` or ``dhan_live._post`` themselves (that wins)."""
    import requests

    real_post = requests.post

    def _post(url, *a, **k):
        if "dhan.co" in str(url):
            raise AssertionError(f"unit test tried a live Dhan call: {url}")
        return real_post(url, *a, **k)

    monkeypatch.setattr(requests, "post", _post)


@pytest.fixture(autouse=True)
def guard_live_sentinel_db(tmp_path, monkeypatch):
    """Safety guard: no unit test may write data/sentinel.db (the live tape ledger).

    Several sentinel suites seeded TEST-* and synthetic NIFTY-FUT bars into the live DB.
    """
    # Keep the basename "sentinel.db": list_recent_bars treats any other name as a
    # mocked-date test DB and changes session isolation. The subdirectory keeps it
    # clear of tests that build their own tmp_path/"sentinel.db".
    db_path = tmp_path / "sentinel_guard" / "sentinel.db"
    monkeypatch.setattr(sentinel_db, "_DB_PATH", db_path)
    sentinel_db.ensure_sentinel_schema()
    yield db_path


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

