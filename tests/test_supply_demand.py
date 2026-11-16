"""Hand-checkable rally-base-drop / drop-base-rally zone rules."""

from analytics.supply_demand import detect_supply_demand, zones_nearest_spot


def _c(o, h, l, c, t="09:15"):
    return {"open": o, "high": h, "low": l, "close": c, "time_ist": t}


def test_drop_base_rally_marks_demand_box():
    # Drop into a tight base near 100, then rally +50.
    bars = []
    bars += [_c(120, 121, 118, 119, "09:15"), _c(119, 120, 115, 116, "09:16")]  # drop into base
    bars += [
        _c(100, 101, 99, 100, "09:17"),
        _c(100, 101.5, 99.5, 100.5, "09:18"),
        _c(100.5, 101, 99, 100, "09:19"),
        _c(100, 101, 99.5, 100.2, "09:20"),
    ]  # base width ~2.5
    bars += [
        _c(101, 110, 100.5, 109, "09:21"),
        _c(109, 120, 108, 118, "09:22"),
        _c(118, 130, 117, 128, "09:23"),
        _c(128, 140, 127, 138, "09:24"),
        _c(138, 150, 137, 148, "09:25"),
        _c(148, 155, 147, 152, "09:26"),
    ]  # impulse away
    # Pad so scanner has room after the impulse (no mitigation).
    bars += [_c(152, 154, 150, 151, f"09:{27 + i:02d}") for i in range(8)]

    zones = detect_supply_demand(bars, impulse_pts=35, base_bars=4, impulse_bars=6)
    demand = [z for z in zones if z["side"] == "demand"]
    assert demand, zones
    z = demand[0]
    assert z["lo"] <= 100 <= z["hi"]
    assert z["rule"] == "drop_base_rally"
    assert z["mitigated"] is False
    assert z["impulse_pts"] >= 35


def test_rally_base_drop_marks_supply_box():
    bars = []
    bars += [_c(80, 85, 79, 84, "09:15"), _c(84, 95, 83, 94, "09:16")]
    bars += [
        _c(100, 101, 99, 100, "09:17"),
        _c(100, 101.5, 99.5, 100.5, "09:18"),
        _c(100.5, 101, 99, 100, "09:19"),
        _c(100, 101, 99.5, 100.2, "09:20"),
    ]
    bars += [
        _c(99, 100, 90, 91, "09:21"),
        _c(91, 92, 80, 81, "09:22"),
        _c(81, 82, 70, 71, "09:23"),
        _c(71, 72, 60, 61, "09:24"),
        _c(61, 62, 55, 56, "09:25"),
        _c(56, 57, 50, 52, "09:26"),
    ]
    bars += [_c(52, 54, 50, 51, f"09:{27 + i:02d}") for i in range(8)]

    zones = detect_supply_demand(bars, impulse_pts=35, base_bars=4, impulse_bars=6)
    supply = [z for z in zones if z["side"] == "supply"]
    assert supply, zones
    z = supply[0]
    assert z["lo"] <= 100 <= z["hi"]
    assert z["rule"] == "rally_base_drop"
    assert z["mitigated"] is False


def test_mitigated_demand_is_dropped_by_default():
    bars = []
    bars += [
        _c(100, 101, 99, 100, "09:17"),
        _c(100, 101.5, 99.5, 100.5, "09:18"),
        _c(100.5, 101, 99, 100, "09:19"),
        _c(100, 101, 99.5, 100.2, "09:20"),
    ]
    bars += [
        _c(101, 120, 100.5, 118, "09:21"),
        _c(118, 130, 117, 128, "09:22"),
        _c(128, 140, 127, 138, "09:23"),
        _c(138, 145, 137, 142, "09:24"),
        _c(142, 148, 141, 146, "09:25"),
        _c(146, 150, 145, 149, "09:26"),
    ]
    # Close back through the base low → mitigated.
    bars += [_c(98, 99, 95, 96, "09:30"), _c(96, 97, 94, 95, "09:31")]
    zones = detect_supply_demand(bars, impulse_pts=35, base_bars=4, impulse_bars=6)
    assert zones == []
    kept = detect_supply_demand(
        bars, impulse_pts=35, base_bars=4, impulse_bars=6, keep_mitigated=True,
    )
    assert any(z["side"] == "demand" and z["mitigated"] for z in kept)


def test_zones_nearest_spot_keeps_closest_each_side():
    zones = [
        {"side": "demand", "mid": 100, "lo": 98, "hi": 102},
        {"side": "demand", "mid": 90, "lo": 88, "hi": 92},
        {"side": "supply", "mid": 120, "lo": 118, "hi": 122},
        {"side": "supply", "mid": 150, "lo": 148, "hi": 152},
    ]
    near = zones_nearest_spot(zones, 110, max_each=1)
    assert len(near) == 2
    assert {z["mid"] for z in near} == {100, 120}


def test_zones_nearest_spot_prefers_demand_below_and_supply_above():
    """A nearer wrong-side box must not win over the correctly sided one."""
    zones = [
        {"side": "demand", "mid": 130, "lo": 128, "hi": 132},  # above spot — wrong side
        {"side": "demand", "mid": 95, "lo": 93, "hi": 97},     # below spot — correct
        {"side": "supply", "mid": 90, "lo": 88, "hi": 92},     # below spot — wrong side
        {"side": "supply", "mid": 125, "lo": 123, "hi": 127},  # above spot — correct
    ]
    near = zones_nearest_spot(zones, 110, max_each=1)
    assert {z["mid"] for z in near} == {95, 125}
    assert all(
        (z["side"] == "demand" and z["mid"] <= 110)
        or (z["side"] == "supply" and z["mid"] >= 110)
        for z in near
    )
