"""The dashboard's data pipeline runs offline (demo) and returns a sane bundle.
Streamlit-free, so this exercises the whole path the UI depends on."""

from app.data_access import DashboardData, load_dashboard


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


def test_load_dashboard_banknifty():
    d = load_dashboard(symbol="BANKNIFTY", window="monthly")
    assert isinstance(d, DashboardData)
    assert d.symbol == "BANKNIFTY"
    assert d.levels.spot >= 40000.0
    assert d.n_contracts > 0

