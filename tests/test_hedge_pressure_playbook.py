"""Gated hedge-pressure playbook (option A) — never unconditional defend."""

from regime.classify import classify
from regime.playbook import hedge_pressure_sentence


def test_hedge_pressure_gates_when_spot_near_wall():
    view = classify(
        spot=24050,
        call_wall=24100,
        put_wall=23800,
        flip=23950,
        regime="POSITIVE",
        total_net_gex=1e9,
        magnet=24000,
        proximity_pts=100,
        total_net_dex=-5e8,  # agrees with call-heavy → negative DEX
    )
    assert view.hedge_pressure is not None
    assert view.hedge_pressure_strike == 24100
    assert "dealers will defend" not in view.hedge_pressure.lower()
    assert "unmeasured" in view.hedge_pressure.lower()
    assert "short_call_short_put" in view.hedge_pressure


def test_hedge_pressure_closed_when_far_from_walls():
    view = classify(
        spot=25000,
        call_wall=24100,
        put_wall=23800,
        flip=23950,
        regime="POSITIVE",
        total_net_gex=1e9,
        magnet=24000,
        proximity_pts=100,
    )
    assert view.hedge_pressure is None


def test_dex_sign_alone_does_not_open_gate():
    # Far from walls; large DEX must not open hedge-pressure.
    view = classify(
        spot=25000,
        call_wall=24100,
        put_wall=23800,
        flip=23950,
        regime="POSITIVE",
        total_net_gex=1e9,
        magnet=24000,
        proximity_pts=100,
        total_net_dex=9e12,
    )
    assert view.hedge_pressure is None


def test_dex_conflict_de_sizes_bias():
    view = classify(
        spot=24000,
        call_wall=24500,
        put_wall=23500,
        flip=23900,
        regime="POSITIVE",
        total_net_gex=1e9,
        proximity_pts=50,  # far enough that gate may still open on call wall? 500 pts away
        total_net_dex=1e9,  # disagrees with call-heavy expectation
    )
    assert view.dex_conflict is True
    assert "GEX/DEX conflict" in view.bias
    assert "dealers will defend" not in view.headline.lower()


def test_cover_frac_strengthens_copy_without_defend_wording():
    text = hedge_pressure_sentence(24100, cover_known=True, cover_frac=0.12)
    assert "observed covering" in text
    assert "dealers will defend" not in text.lower()


def test_zero_walls_do_not_open_gate():
    # near_book_expired leaves walls at 0 — must not invent hedge pressure
    view = classify(
        spot=24000,
        call_wall=0.0,
        put_wall=0.0,
        flip=None,
        regime="POSITIVE",
        total_net_gex=1e9,
        magnet=0.0,
        proximity_pts=100,
    )
    assert view.hedge_pressure is None


def test_from_levels_passes_dex_and_cover():
    from types import SimpleNamespace
    from regime.classify import from_levels

    levels = SimpleNamespace(
        spot=24050.0,
        call_wall=24100.0,
        put_wall=23800.0,
        flip=23950.0,
        regime="POSITIVE",
        total_net_gex=1e9,
        magnet=24000.0,
        max_pain=24000.0,
        total_net_dex=-1e8,
        call_delta_wall=24100.0,
        put_delta_wall=23800.0,
        underlying="NIFTY",
    )
    view = from_levels(levels, cover_known=True, cover_frac=0.10)
    assert view.hedge_pressure is not None
    assert "observed covering" in view.hedge_pressure
    assert view.total_net_dex == -1e8
