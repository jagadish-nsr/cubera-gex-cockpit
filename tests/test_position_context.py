"""Participant holdings counts and the prior value-area line."""

from analytics.position_context import holdings_table, value_area_line


def test_holdings_are_longs_and_shorts_only():
    table = holdings_table({
        "Client": {"fut_long": 10, "fut_short": 4, "opt_call_long": 1, "opt_call_short": 2,
                   "opt_put_long": 3, "opt_put_short": 5},
        "FII": {"fut_long": 8, "fut_short": 9},
    }, "2026-10-23")
    assert [row["participant"] for row in table["rows"]] == ["Client", "FII", "DII", "Pro"]
    assert table["rows"][0]["fut_long"] == 10
    assert table["rows"][0]["opt_put_short"] == 5
    assert table["rows"][2]["fut_long"] is None
    assert "dealer_sign_hint" not in table


def test_value_area_line_says_whether_spot_is_inside():
    prior = {"poc": 22600, "vah": 22700, "val": 22500}
    inside = value_area_line(prior, 22620, "2026-10-23")
    outside = value_area_line(prior, 22800, "2026-10-23")
    assert inside["inside"] is True
    assert "inside" in inside["text"]
    assert outside["inside"] is False
    assert "outside" in outside["text"]
    assert value_area_line(None, 1, None)["text"] == "No prior value area."
