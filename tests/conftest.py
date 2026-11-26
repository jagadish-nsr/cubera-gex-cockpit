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


@pytest.fixture(scope="session")
def _market_snapshot_copy(tmp_path_factory):
    import shutil
    import sqlite3
    import data.market_snapshot as ms

    dst = tmp_path_factory.mktemp("market_snapshot") / "market_snapshot.db"
    if ms.DB_PATH.exists():
        src = sqlite3.connect(str(ms.DB_PATH))
        try:
            out = sqlite3.connect(str(dst))
            src.backup(out)
            out.close()
        finally:
            src.close()
    return dst


@pytest.fixture(autouse=True)
def guard_live_market_snapshot_db(_market_snapshot_copy, monkeypatch):
    """No unit test may write data/journal/market_snapshot.db (the sealed session closes).

    A spot-tick test's background save wrote NIFTY 25,250 as a ws-tick, and a leaked 27-Oct book
    replaced the 26-Nov row. Promote runs these suites, so the prod file was exposed too. Tests
    read a session copy of the real file, so tests that expect real closes still see them.
    """
    monkeypatch.setattr("data.market_snapshot.DB_PATH", _market_snapshot_copy)
    yield _market_snapshot_copy


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

