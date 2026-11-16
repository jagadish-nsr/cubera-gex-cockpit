"""Hand-checkable structural reaction map aggregation."""

from analytics.structural_map import build_structural_map, mark_ladder_structural


def test_build_structural_map_lists_known_levels_and_spot_distance():
    out = build_structural_map(
        25150,
        call_wall=25300,
        put_wall=25050,
        gamma_flip=25140,
        prior_marks={"high": 25280, "low": 25040, "close": 25120, "date": "2026-11-14"},
        day_ohlc={"high": 25268, "low": 25092, "open": 25100},
        value_area={"poc": 25170, "vah": 25210, "val": 25120, "inside": True, "date": "2026-11-14"},
        strike_step=50,
    )
    ids = {lv["id"] for lv in out["levels"]}
    assert "call_wall" in ids
    assert "put_wall" in ids
    assert "gamma_flip" in ids
    assert "prior_high" in ids
    assert "prior_low" in ids
    assert "value_area" in ids
    assert "poc" not in ids  # carried on the value-area band
    assert out["spot"] == 25150.0
    chips = {c["id"]: c for c in out["chips"]}
    assert chips["call_wall"]["dist_pts"] == 150.0
    assert chips["call_wall"]["dist_text"] == "+150 pts"
    assert "inside prior value area" in out["summary"].lower()
    assert "Reaction candidates only" in out["summary"]


def test_overlap_clusters_wall_with_prior_high_within_one_step():
    out = build_structural_map(
        25150,
        call_wall=25300,
        prior_marks={"high": 25280, "low": 24900, "close": 25100, "date": "2026-11-14"},
        strike_step=50,
    )
    # 25300 vs 25280 = 20 pts ≤ 50 → overlap
    assert any(
        set(row["members"]) >= {"call_wall", "prior_high"}
        for row in out["overlaps"]
    )
    chips = {c["id"]: c for c in out["chips"]}
    assert chips["call_wall"]["overlap"] is True
    assert chips["prior_high"]["overlap"] is True


def test_no_overlap_when_levels_are_farther_than_tolerance():
    out = build_structural_map(
        25150,
        call_wall=25300,
        put_wall=25000,
        prior_marks={"high": 25600, "low": 24800, "close": 25100},
        strike_step=50,
    )
    assert out["overlaps"] == []
    assert all(not c["overlap"] for c in out["chips"])


def test_value_area_band_overlaps_point_inside_band():
    out = build_structural_map(
        25150,
        gamma_flip=25160,
        value_area={"poc": 25170, "vah": 25210, "val": 25120, "inside": True},
        strike_step=50,
    )
    assert any(
        set(row["members"]) >= {"gamma_flip", "value_area"}
        for row in out["overlaps"]
    )


def test_overlap_does_not_chain_across_the_whole_ladder():
    """Levels spaced ~one step apart must not collapse into one giant overlap."""
    out = build_structural_map(
        25150,
        call_wall=25300,
        put_wall=25050,
        gamma_flip=25140,
        prior_marks={"high": 25280, "low": 25040, "close": 25120, "date": "2026-11-14"},
        day_ohlc={"high": 25268, "low": 25092, "open": 25100},
        value_area={"poc": 25170, "vah": 25210, "val": 25120, "inside": True},
        strike_step=50,
    )
    # No cluster may span more than one strike step.
    for row in out["overlaps"]:
        assert row["hi"] - row["lo"] <= 50.0 + 1e-6
    # Call wall still overlaps prior high; put wall still overlaps prior low.
    assert any(set(row["members"]) >= {"call_wall", "prior_high"} for row in out["overlaps"])
    assert any(set(row["members"]) >= {"put_wall", "prior_low"} for row in out["overlaps"])
    assert not any(
        {"call_wall", "put_wall"}.issubset(set(row["members"]))
        for row in out["overlaps"]
    )
    assert not any(
        {"put_wall", "gamma_flip"}.issubset(set(row["members"]))
        for row in out["overlaps"]
    )


def test_build_structural_map_includes_vp_and_supply_demand():
    out = build_structural_map(
        25150,
        call_wall=25300,
        volume_profile={
            "day": "2026-11-14",
            "vpoc": 25180,
            "vah": 25240,
            "val": 25120,
            "hvns": [{"price": 25220, "volume": 100}],
            "lvns": [{"price": 25140, "volume": 20}],
        },
        session_vpoc={"price": 25155, "day": "2026-11-15"},
        supply_demand=[
            {"side": "demand", "lo": 25040, "hi": 25080, "day": "2026-11-14", "impulse_pts": 60},
            {"side": "supply", "lo": 25260, "hi": 25310, "day": "2026-11-14", "impulse_pts": 55},
        ],
        strike_step=50,
    )
    ids = {lv["id"] for lv in out["levels"]}
    assert "vpoc" in ids
    assert "hvn_0" in ids
    assert "lvn_0" in ids
    assert "session_vpoc" in ids
    assert "demand_0" in ids
    assert "supply_0" in ids
    assert "volume-profile" in out["summary"]
    assert "supply/demand" in out["summary"]
    chips = {c["id"]: c for c in out["chips"]}
    assert chips["demand_0"]["lo"] == 25040.0
    assert chips["supply_0"]["hi"] == 25310.0


def test_mark_ladder_structural_tags_nearest_rows():
    ladder = [
        {"strike": 25300.0},
        {"strike": 25200.0},
        {"strike": 25150.0},
        {"strike": 25100.0},
        {"strike": 25050.0},
    ]
    smap = build_structural_map(
        25150,
        call_wall=25300,
        prior_marks={"high": 25310, "low": 25040, "close": 25100},
        value_area={"poc": 25170, "vah": 25210, "val": 25100, "inside": True},
        day_ohlc={"high": 25200, "low": 25050},
        strike_step=50,
    )
    mark_ladder_structural(ladder, smap, 50)
    by_strike = {row["strike"]: row for row in ladder}
    assert by_strike[25300.0]["is_prior_high"] is True
    assert by_strike[25200.0]["is_vah"] is True or by_strike[25200.0]["is_day_high"] is True
    assert by_strike[25100.0]["is_val"] is True or by_strike[25100.0]["is_prior_close"] is True
    assert by_strike[25050.0]["is_day_low"] is True
