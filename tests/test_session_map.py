"""Hand checks for the session map: numbers and locations, no direction arrow."""

from analytics.session_map import (
    crossover_watch,
    describe_futures,
    magnet_closes,
    prior_marks_from_spots,
    strike_doi_table,
)


def test_strike_doi_is_a_number_inside_the_band():
    rows = strike_doi_table([
        {"strike": 22600, "call_doi": 1200.4, "put_doi": -80},
        {"strike": 24000, "call_doi": 5, "put_doi": 5},
    ], spot=22600, band_pts=200)
    assert rows == [{"strike": 22600.0, "call_doi": 1200, "put_doi": -80}]


def test_magnet_path_keeps_the_last_positive_print():
    rows = magnet_closes([
        ("2026-10-21", [22600, 22650]),
        ("2026-10-22", [None, 0]),
        ("2026-10-23", [22700]),
    ])
    assert rows == [
        {"date": "2026-10-21", "magnet": 22650.0},
        {"date": "2026-10-23", "magnet": 22700.0},
    ]


def test_prior_session_marks():
    marks = prior_marks_from_spots([22600, 22680, 22540, 22610])
    assert marks == {"high": 22680.0, "low": 22540.0, "close": 22610.0}


def test_futures_state_names_the_pair_without_a_bias_field():
    up = describe_futures(12.5, 4000, price=22650)
    down = describe_futures(-8, 1500)
    cover = describe_futures(4, -900)
    unwind = describe_futures(-3, -200)
    assert up["state"] == "Price up, open interest up"
    assert down["state"] == "Price down, open interest up"
    assert cover["state"] == "Price up, open interest down"
    assert unwind["state"] == "Price down, open interest down"
    assert "bias" not in up
    assert describe_futures(None, 10) is None


def test_crossover_watch_is_a_parity_location():
    strikes = [
        {"strike": 22600, "call_ltp": 140, "put_ltp": 90},
        {"strike": 22500, "call_ltp": 40, "put_ltp": 110},
    ]
    watch = crossover_watch(strikes, spot=22610, magnet=22500)
    by_name = {row["name"]: row for row in watch["anchors"]}
    assert by_name["ATM"]["side"] == "call_rich"
    assert "above this strike" in by_name["ATM"]["location"]
    assert by_name["Magnet"]["side"] == "put_rich"
    blob = " ".join(row["location"] for row in watch["anchors"]).lower()
    assert "pin" not in blob
    assert "blast" not in blob
