"""Book-balance classification. The sign is call gamma minus put gamma.

A put-gamma strike below the flip is concentration on the put side of the
balance, not a failed floor. Disagreement between the aggregate sign and the
spot-vs-flip side is a CONFLICT -> de-size.
"""

from regime.classify import classify


def test_call_heavy_book_between_strikes():
    # spot above flip, inside the wall channel -> call gamma larger than put gamma.
    v = classify(
        spot=7578, call_wall=7600, put_wall=7000, flip=7465,
        regime="POSITIVE", total_net_gex=40e9, magnet=7600, max_pain=7000,
    )
    assert v.flip_dist_pts == -113      # 7465 - 7578
    assert v.flip_side == "below"
    assert v.in_positive_zone is True   # spot >= flip
    assert v.call_wall_dist_pts == 22   # 7600 - 7578
    assert v.put_wall_dist_pts == -578  # 7000 - 7578
    assert v.put_wall_role == "put concentration, below balance"  # 7000 < flip 7465
    assert v.call_wall_role == "call concentration"
    assert v.conflict is False
    assert v.bias == "call gamma larger than put gamma"
    assert v.headline == (
        "Call-heavy book, flip 113pts below, call wall 22 above — "
        "call gamma larger than put gamma"
    )
    assert v.mode_label == "CALL-HEAVY"
    assert "not a fade signal" in v.mode_plain.lower()
    assert "7,000" in v.mode_plain and "7,600" in v.mode_plain


def test_put_heavy_book():
    # spot below flip, negative regime -> put gamma larger than call gamma.
    v = classify(
        spot=5000, call_wall=5200, put_wall=4800, flip=5100,
        regime="NEGATIVE", total_net_gex=-1e9,
    )
    assert v.flip_dist_pts == 100
    assert v.flip_side == "above"
    assert v.in_positive_zone is False
    assert v.conflict is False
    assert v.bias == "put gamma larger than call gamma"
    assert v.headline.startswith("Put-heavy book, flip 100pts above")
    assert v.mode_label == "PUT-HEAVY"
    assert "does not by itself" in v.mode_plain.lower()


def test_conflict_desizes():
    # POSITIVE aggregate but spot BELOW flip -> regime/zone conflict -> de-size.
    v = classify(
        spot=5000, call_wall=5200, put_wall=4800, flip=5100,
        regime="POSITIVE", total_net_gex=1e8,
    )
    assert v.in_positive_zone is False
    assert v.conflict is True
    assert "de-size" in v.bias
    assert any("conflict" in n.lower() for n in v.notes)
    assert v.mode_label == "CAUTION"


def test_flip_missing_is_flagged_not_invented():
    v = classify(
        spot=6000, call_wall=6100, put_wall=5800, flip=None,
        regime="POSITIVE", total_net_gex=5e8,
    )
    assert v.flip_side is None
    assert v.in_positive_zone is None
    assert v.put_wall_role == "unknown"
    assert "flip" in v.headline.lower()
    assert any("flip" in n.lower() for n in v.notes)


def test_plain_mode_handles_none_magnet():
    # magnet/max_pain default to None — the plain-mode text must not crash.
    v = classify(
        spot=100, call_wall=110, put_wall=90, flip=95,
        regime="POSITIVE", total_net_gex=1e9,
    )
    assert v.mode_label == "CALL-HEAVY"
    assert v.mode_plain  # non-empty, no formatting crash
