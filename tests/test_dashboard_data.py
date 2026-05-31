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
    # levels are real strikes near spot
    assert abs(d.levels.call_wall - d.levels.spot) < d.levels.spot * 0.2
