"""The dashboard's data pipeline runs offline (demo) and returns a sane bundle.
Streamlit-free, so this exercises the whole path the UI depends on."""

import pickle
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.data_access import DashboardData, _bundle_from_journal_pickle, load_dashboard


def test_load_dashboard_demo():
    d = load_dashboard(window="0dte_to_next_monthly", use_demo=True)
    assert isinstance(d, DashboardData)
    assert d.source == "DEMO-SYNTHETIC"
    assert d.levels.spot > 0
    assert d.n_contracts > 0
    assert d.view.headline                      # one-sentence playbook populated
    assert d.levels.regime in {"POSITIVE", "NEGATIVE", "FLAT"}
    assert abs(d.levels.call_wall - d.levels.spot) < d.levels.spot * 0.2


def test_load_dashboard_sensex():
    d = load_dashboard(symbol="SENSEX", window="current_week")
    assert isinstance(d, DashboardData)
    assert d.symbol == "SENSEX"
    assert d.levels.spot >= 70000.0
    assert d.n_contracts > 0
    assert d.levels.call_wall > 0
    assert d.levels.put_wall > 0
    assert abs(d.levels.call_wall - d.levels.spot) < d.levels.spot * 0.2


def test_journal_pickle_wrapper_is_the_last_book():
    """The fetcher writes {"data": bundle}. Treating that dict as the book drops it."""
    fetched = datetime.now(timezone.utc) - timedelta(minutes=2)
    book = SimpleNamespace(
        source="dhan",
        levels=SimpleNamespace(spot=22709.0),
        is_degraded=False,
        staleness_seconds=0.0,
        degraded_reason="",
        derivatives_frozen=False,
        oi_strikes=None,
    )
    restored = _bundle_from_journal_pickle(
        {"data": book, "fetched_at": fetched, "symbol": "NIFTY"},
        "chain timeout",
    )
    assert restored is book
    assert book.is_degraded is True
    assert book.derivatives_frozen is True
    assert book.staleness_seconds >= 60.0
    assert book.levels.spot == 22709.0
    assert _bundle_from_journal_pickle(
        {"data": SimpleNamespace(source="DEMO-SYNTHETIC", levels=SimpleNamespace(spot=24500.0))},
        "chain timeout",
    ) is None


def test_chain_timeout_serves_the_journal_snapshot(monkeypatch, tmp_path):
    """A 35s miss with an empty in-memory LKGS still has the last persisted book."""
    from resilience.circuit_breaker import get_circuit_breaker
    from resilience.lkgs import get_lkgs_manager

    book = SimpleNamespace(
        source="dhan",
        levels=SimpleNamespace(spot=22709.0),
        is_degraded=False,
        staleness_seconds=0.0,
        degraded_reason="",
        derivatives_frozen=False,
        oi_strikes=None,
        circuit_state="CLOSED",
    )
    journal = tmp_path / "data" / "journal"
    journal.mkdir(parents=True)
    with open(journal / "latest_snap_NIFTY.pkl", "wb") as f:
        pickle.dump(
            {"data": book, "symbol": "NIFTY", "fetched_at": datetime.now(timezone.utc)},
            f,
        )

    lkgs = get_lkgs_manager()
    lkgs.clear("NIFTY")
    get_circuit_breaker("NIFTY").reset()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "app.data_access._fetch_live_chain",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("option chain exceeded 35s")),
    )
    monkeypatch.setattr("data.news.fetch_news", lambda *a, **k: None)
    monkeypatch.setattr("data.sectors.fetch_sector_snap", lambda *a, **k: None)
    monkeypatch.setattr("data.nse.fetch_india_vix", lambda *a, **k: None)
    try:
        restored = load_dashboard(symbol="NIFTY", window="current_week", live_source="nse")
    finally:
        lkgs.clear("NIFTY")
        get_circuit_breaker("NIFTY").reset()
    assert restored.levels.spot == 22709.0
    assert restored.is_degraded is True
    assert restored.derivatives_frozen is True
    assert "last journal snapshot" in restored.degraded_reason


def test_load_dashboard_banknifty():
    d = load_dashboard(symbol="BANKNIFTY", window="monthly")
    assert isinstance(d, DashboardData)
    assert d.symbol == "BANKNIFTY"
    assert d.levels.spot >= 40000.0
    assert d.n_contracts > 0

