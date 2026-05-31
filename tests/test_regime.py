"""Hand-checkable regime classification + one-sentence playbook (Phase 2 logic).

Wall-strength is judged RELATIVE to the flip (gex-math skill): a put wall above
the flip is real support; below it, a speed bump. Regime sign that disagrees
with the spot-vs-flip zone is a CONFLICT -> de-size (CLAUDE.md), never blindly
fade a thesis-aligned move.
"""

from regime.classify import classify


def test_positive_range_fade():
    # spot above flip, inside the wall channel -> positive-gamma range fade.
    v = classify(
        spot=7578, call_wall=7600, put_wall=7000, flip=7465,
        regime="POSITIVE", total_net_gex=40e9, magnet=7600, max_pain=7000,
    )
    assert v.flip_dist_pts == -113      # 7465 - 7578
    assert v.flip_side == "below"
    assert v.in_positive_zone is True   # spot >= flip
    assert v.call_wall_dist_pts == 22   # 7600 - 7578
    assert v.put_wall_dist_pts == -578  # 7000 - 7578
    assert v.put_wall_role == "speed bump"   # put wall 7000 < flip 7465
    assert v.conflict is False
    assert v.bias == "range fade"
    assert v.headline == "Positive gamma, flip 113pts below, call wall 22 above — range fade"
    assert v.mode_label == "RANGE-BOUND"
    assert "range-bound between" in v.mode_plain.lower()
    assert "7,000" in v.mode_plain and "7,600" in v.mode_plain  # support/resistance shown


def test_negative_momentum():
    # spot below flip, negative regime -> momentum / trade with the break.
    v = classify(
        spot=5000, call_wall=5200, put_wall=4800, flip=5100,
        regime="NEGATIVE", total_net_gex=-1e9,
    )
    assert v.flip_dist_pts == 100
    assert v.flip_side == "above"
    assert v.in_positive_zone is False
    assert v.conflict is False
    assert v.bias.startswith("momentum")
    assert v.headline.startswith("Negative gamma, flip 100pts above")
    assert v.mode_label == "TRENDING (volatile)"
    assert "amplif" in v.mode_plain.lower() or "fast moves" in v.mode_plain.lower()


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
