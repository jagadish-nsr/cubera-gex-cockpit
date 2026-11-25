"""Hand check for the margin-stress map.

Bar 1 adds 100 OI at 100. r = 0.05 on both sides, f = 0.8.
Long stress = 100 * (1 - 0.04) = 96. Short stress = 104.
Bar 2 trades 96 and OI falls 40 → observed long unwind 40.
Bar 3 OI falls 20 without reaching 96 or 104 → no further observed mark.
Remaining inventory is 40 lots on each side.
"""

import pytest

from analytics.liquidation_map import build_liquidation_map, stress_prices


def _book():
    return [
        {"close": 100.0, "high": 100.0, "low": 100.0, "doi": 100.0},
        {"close": 96.0, "high": 100.0, "low": 96.0, "doi": -40.0},
        {"close": 100.0, "high": 102.0, "low": 98.0, "doi": -20.0},
    ]


def _map(**kwargs):
    base = dict(
        bars=_book(),
        margin_frac_long=0.05,
        margin_frac_short=0.05,
        square_off_frac=0.8,
        bin_step=1.0,
        symbol="NIFTY",
        spot=100.0,
    )
    base.update(kwargs)
    bars = base.pop("bars")
    return build_liquidation_map(bars, **base)


def test_three_bar_book_marks_only_the_overlapping_long_unwind():
    out = _map()
    by_price = {row["price"]: row for row in out["bins"]}
    assert by_price[96.0]["long_heat"] == 40.0
    assert by_price[96.0]["long_unwind"] == 40.0
    assert by_price[104.0]["short_heat"] == 40.0
    assert by_price[104.0]["short_unwind"] == 0.0
    assert out["client_split"] is False
    assert "squared off" in out["reading"]


def test_unequal_long_and_short_fractions_put_each_side_on_its_own_stress():
    # r_long 0.05 → long stress 100 * (1 - 0.04) = 96.
    # r_short 0.10 → short stress 100 * (1 + 0.08) = 108.
    assert stress_prices(100.0, 0.05, 0.10, 0.8) == pytest.approx((96.0, 108.0))

    out = _map(margin_frac_long=0.05, margin_frac_short=0.10)
    by_price = {row["price"]: row for row in out["bins"]}
    assert by_price[96.0]["long_heat"] == 40.0
    assert by_price[96.0]["long_unwind"] == 40.0
    assert by_price[108.0]["short_heat"] == 40.0
    assert by_price[108.0]["short_unwind"] == 0.0
    assert by_price.get(104.0, {}).get("short_heat", 0.0) == 0.0
    assert out["margin_frac_long"] == 0.05
    assert out["margin_frac_short"] == 0.10


def test_missing_margin_fraction_raises():
    with pytest.raises(ValueError, match="margin_frac is unset"):
        _map(margin_frac_long=None)
    with pytest.raises(ValueError, match="margin_frac is unset"):
        _map(margin_frac_short=None)


def test_stale_participant_file_uses_total_oi():
    out = _map(
        client_long=10,
        client_short=10,
        total_long=100,
        total_short=100,
        participant_age_sessions=4,
        participant_date="2026-11-01",
    )
    by_price = {row["price"]: row for row in out["bins"]}
    assert out["participant_stale"] is True
    assert out["client_split"] is False
    assert by_price[96.0]["long_heat"] == 40.0


def test_fresh_client_share_scales_both_sides():
    out = _map(
        client_long=50,
        client_short=25,
        total_long=100,
        total_short=100,
        participant_age_sessions=1,
    )
    by_price = {row["price"]: row for row in out["bins"]}
    assert out["client_split"] is True
    assert by_price[96.0]["long_heat"] == 20.0
    assert by_price[96.0]["long_unwind"] == 20.0
    assert by_price[104.0]["short_heat"] == 10.0
    assert by_price[104.0]["short_unwind"] == 0.0


def test_sensex_never_uses_a_client_split():
    out = _map(
        symbol="SENSEX",
        client_long=50,
        client_short=50,
        total_long=100,
        total_short=100,
        participant_age_sessions=1,
    )
    assert out["client_split"] is False
    assert out["long_share"] == 1.0


def test_roll_window_suppresses_observed_marks_until_aggregate_oi_falls():
    held = _map(sessions_to_expiry=2, agg_doi=10)
    assert held["roll_window"] is True
    assert held["observed_suppressed"] is True
    assert all(row["long_unwind"] == 0 and row["short_unwind"] == 0 for row in held["bins"])
    assert any(row["long_heat"] == 40 for row in held["bins"])

    fell = _map(sessions_to_expiry=2, agg_doi=-5)
    assert fell["observed_suppressed"] is False
    assert any(row["long_unwind"] == 40 for row in fell["bins"])


def test_margin_frac_is_span_loss_over_notional_plus_exposure():
    # Spot 25,000, lot 65 → notional 1,625,000.
    # Worst scenario loss 16,250 per lot → SPAN part 0.01.
    # Exposure 3% → r = 0.04.
    from analytics.liquidation_map import margin_frac_from_span

    r = margin_frac_from_span(16_250, 25_000, 65, 0.03)
    assert r == pytest.approx(0.04)


def test_margin_frac_refuses_missing_span_loss():
    from analytics.liquidation_map import margin_frac_from_span

    with pytest.raises(ValueError, match="SPAN scan loss is missing"):
        margin_frac_from_span(None, 25_000, 65, 0.03)


def test_span_parser_gives_long_and_short_from_the_right_scenarios():
    # Real NIFTY 2026-12-29 risk array from nsccl.20261123.s.spn (per unit, losses positive).
    # Long loss = max(a) = 2383.27 on the down-3/3 scenario.
    # Short loss = -min(a) = 2363.27 on the up-3/3 scenario.
    from analytics.span_file import margin_fracs, parse_futures_span

    ra = [10.00, 10.00, -781.09, -781.09, 801.09, 801.09, -1572.18, -1572.18,
          1592.18, 1592.18, -2363.27, -2363.27, 2383.27, 2383.27, -1657.79, 1664.79]
    values = "".join(f"<a>{v:.2f}</a>" for v in ra)
    text = (
        "<spanFile><fileFormat>4.00</fileFormat><created>202611231709</created>"
        "<futPf><pfCode>NIFTY</pfCode><fut><pe>20261229</pe><p>25504.70</p>"
        "<cvf>1.00</cvf><setlDate>20261229</setlDate>"
        f"<ra>{values}<d>1</d></ra></fut></futPf></spanFile>"
    )
    out = parse_futures_span(text, indices=("NIFTY",))
    nifty = out["contracts"]["NIFTY"]
    assert out["file_date"] == "2026-11-23"
    assert nifty["expiry"] == "2026-12-29"
    assert nifty["worst_long_per_unit"] == pytest.approx(2383.27)
    assert nifty["worst_short_per_unit"] == pytest.approx(2363.27)

    r_long, r_short = margin_fracs(nifty, 0.03)
    assert r_long == pytest.approx(0.123444, abs=1e-6)
    assert r_short == pytest.approx(0.122660, abs=1e-6)


def test_feed_stays_blank_when_no_span_file_is_present(tmp_path, monkeypatch):
    import config
    from analytics.liquidation_feed import load_liquidation_map

    monkeypatch.setattr(config, "SPAN_DIR", tmp_path)
    out = load_liquidation_map("NIFTY")
    assert out["empty"] is True
    assert out["reason"] == "span_file_missing"
    assert out["bins"] == []
    assert "SPAN file" in out["reading"]


def test_feed_hides_bands_when_the_tape_is_not_the_span_session(monkeypatch):
    import analytics.liquidation_feed as feed

    fake_span = {
        "file_date": "2026-11-23",
        "contracts": {
            "NIFTY": {
                "expiry": "2026-12-29",
                "price": 25504.7,
                "span_frac_long": 0.093444,
                "span_frac_short": 0.09266,
            }
        },
    }

    def _no_frame(*_args, **_kwargs):
        raise AssertionError("bars must not load when the tape is a different session")

    monkeypatch.setattr(feed, "load_latest_span", lambda _d: ("nsccl.20261123.s.spn", fake_span))
    monkeypatch.setattr(feed, "available_futures_days", lambda _s: ["2026-10-27"])
    monkeypatch.setattr(feed, "load_futures_bars", _no_frame)

    out = feed.load_liquidation_map("NIFTY")
    assert out["empty"] is True
    assert out["reason"] == "tape_span_mismatch"
    assert out["bins"] == []
    assert out["tape_day"] == "2026-10-27"
    assert out["span_date"] == "2026-11-23"
    assert out["margin_frac_long"] == pytest.approx(0.123444, abs=1e-6)


def test_feed_places_inventory_opened_on_an_earlier_session(monkeypatch):
    import pandas as pd

    import analytics.liquidation_feed as feed
    import config

    # Two stored sessions. 20 Nov: OI 100 opening print, then a rise to 200 at 100 seeds
    # +100 on both sides. 23 Nov (SPAN date): OI falls 200 → 160 at 93 and unwinds 40.
    frames = {
        "2026-11-20": pd.DataFrame([
            {"close": 100.0, "high": 100.0, "low": 100.0, "oi": 100.0},
            {"close": 100.0, "high": 100.0, "low": 100.0, "oi": 200.0},
        ]),
        "2026-11-23": pd.DataFrame([
            {"close": 100.0, "high": 100.0, "low": 100.0, "oi": 200.0},
            {"close": 93.0, "high": 93.0, "low": 93.0, "oi": 160.0},
        ]),
    }
    fake_span = {
        "file_date": "2026-11-23",
        "contracts": {"NIFTY": {
            "expiry": "2026-12-29", "price": 100.0,
            "span_frac_long": 0.04, "span_frac_short": 0.04,
        }},
    }
    monkeypatch.setattr(config, "FUTURES_SQUARE_OFF_FRAC", 1.0)
    monkeypatch.setattr(config, "strike_step_for", lambda _s: 1.0)
    monkeypatch.setattr(feed, "load_latest_span", lambda _d: ("nsccl.20261123.s.spn", fake_span))
    monkeypatch.setattr(feed, "available_futures_days", lambda _s: sorted(frames))
    monkeypatch.setattr(feed, "load_futures_bars", lambda _s, d: frames[d])
    monkeypatch.setattr(feed, "_participant", lambda _s, _a: {
        "client_long": None, "client_short": None, "total_long": None, "total_short": None,
        "participant_age_sessions": None, "participant_date": None,
    })

    out = feed.load_liquidation_map("NIFTY")
    # r = 0.04 + 0.03 = 0.07 on both sides, f = 1 → long stress 93, short stress 107.
    by_price = {row["price"]: row for row in out["bins"]}
    assert out["sessions_used"] == 2
    assert out["tape_first_day"] == "2026-11-20"
    assert out["tape_day"] == "2026-11-23"
    assert out["opening_oi_unplaced"] == 100.0
    assert by_price[93.0]["long_heat"] == 60.0
    assert by_price[93.0]["long_unwind"] == 40.0
    assert by_price[107.0]["short_heat"] == 60.0
    assert by_price[107.0]["short_unwind"] == 0.0


def test_opening_absolute_oi_is_not_placed_at_the_first_print():
    out = build_liquidation_map(
        [
            {"close": 100.0, "high": 101.0, "low": 99.0, "oi": 500.0},
            {"close": 100.0, "high": 101.0, "low": 99.0, "oi": 500.0},
        ],
        margin_frac_long=0.05,
        margin_frac_short=0.05,
        square_off_frac=0.8,
        bin_step=1.0,
        symbol="NIFTY",
    )
    assert out["empty"] is True
    assert out["opening_oi_unplaced"] == 500.0
