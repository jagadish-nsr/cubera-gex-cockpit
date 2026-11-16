"""Hand-checkable volume-at-price profile and HVN/LVN nodes."""

from analytics.volume_profile import detect_nodes, profile_from_bars, volume_histogram


def _bar(low, high, close, volume):
    return {"open": low, "high": high, "low": low, "close": close, "volume": volume}


def test_volume_histogram_puts_peak_volume_at_dense_bin():
    # 30 bars around 100 with heavy volume; 20 bars around 120 with light volume.
    bars = [_bar(99, 101, 100, 1000) for _ in range(30)]
    bars += [_bar(119, 121, 120, 100) for _ in range(20)]
    hist = volume_histogram(bars, bin_pts=1.0)
    assert hist is not None
    assert abs(hist["vpoc"] - 100.0) <= 1.0
    assert hist["val"] <= hist["vpoc"] <= hist["vah"]
    assert hist["total_volume"] == 30 * 1000 + 20 * 100


def test_detect_nodes_finds_secondary_hvn_and_valley_lvn():
    # Two peaks at 100 and 130, thin middle at 115.
    bars = []
    bars += [_bar(99, 101, 100, 800) for _ in range(25)]
    bars += [_bar(114, 116, 115, 50) for _ in range(10)]
    bars += [_bar(129, 131, 130, 600) for _ in range(20)]
    hist = volume_histogram(bars, bin_pts=1.0)
    nodes = detect_nodes(hist, hvn_frac=0.50, lvn_frac=0.35, max_hvn=2, max_lvn=2)
    assert nodes["vpoc"] is not None
    assert abs(nodes["vpoc"] - 100.0) <= 1.5
    assert nodes["hvns"], "expected at least one secondary HVN near 130"
    assert any(abs(h["price"] - 130.0) <= 2.0 for h in nodes["hvns"])
    assert nodes["lvns"], "expected an LVN in the thin middle"
    assert any(110.0 <= l["price"] <= 120.0 for l in nodes["lvns"])


def test_profile_from_bars_returns_none_when_too_few_bars():
    bars = [_bar(100, 101, 100.5, 10) for _ in range(5)]
    assert profile_from_bars(bars, bin_pts=1.0) is None
