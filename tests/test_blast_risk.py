"""Hand-checks for expiry blast → recommendation risk conditioner (map, not direction)."""

from types import SimpleNamespace

from app.blast_risk import (
    SIZE_MULT_ACTIVE,
    SIZE_MULT_COILING,
    SHORT_NEAR_MAGNET_PENALTY,
    apply_active_short_magnet_penalty,
    blast_risk_adjust,
    short_premium_near_magnet,
)


def test_non_expiry_is_noop():
    adj = blast_risk_adjust({"is_expiry": False, "state": "TRIGGERED", "score": 80}, 23700)
    assert adj["applies"] is False
    assert adj["size_mult"] == 1.0
    assert adj["tag"] == ""


def test_coiling_mild_desize():
    adj = blast_risk_adjust(
        {
            "is_expiry": True,
            "state": "COILING",
            "score": 48,
            "badge": "GAMMA BLAST: COILING / IMMINENT",
            "primary_strike": 23700,
        },
        spot=23680,
    )
    assert adj["coiling"] is True
    assert adj["active"] is False
    assert adj["applies"] is True
    assert adj["size_mult"] == SIZE_MULT_COILING
    assert "COILING" in adj["tag"]
    assert "map" in adj["risk_line"].lower() or "Nearness" in adj["risk_line"]
    assert "dealers" not in adj["risk_line"].lower()


def test_active_stronger_desize():
    adj = blast_risk_adjust(
        {
            "is_expiry": True,
            "state": "TRIGGERED",
            "score": 72,
            "badge": "GAMMA BLAST: ACTIVE",
            "primary_strike": 23750,
        },
        spot=23780,
    )
    assert adj["active"] is True
    assert adj["coiling"] is False
    assert adj["size_mult"] == SIZE_MULT_ACTIVE
    assert "ACTIVE" in adj["tag"]
    assert "magnet" in adj["risk_line"].lower()


def test_no_trigger_on_expiry_noop():
    adj = blast_risk_adjust(
        {"is_expiry": True, "state": "NO_TRIGGER", "score": 20, "badge": "GAMMA BLAST: INACTIVE"},
        23700,
    )
    assert adj["applies"] is False
    assert adj["size_mult"] == 1.0


def test_short_near_magnet_and_penalty_never_raises_score():
    idea = SimpleNamespace(play_badge="VANNA-CHARM", strike=23700.0, quality_score=80, score_reason="")
    assert short_premium_near_magnet(idea, 23700.0, 50) is True
    longish = SimpleNamespace(play_badge="MOMENTUM", strike=23700.0, quality_score=80)
    assert short_premium_near_magnet(longish, 23700.0, 50) is False

    adj = {"active": True, "primary_strike": 23700.0}
    new = apply_active_short_magnet_penalty(idea, adj, 50.0)
    assert new == 80 - SHORT_NEAR_MAGNET_PENALTY
    assert idea.quality_score == new
    # Inactive: no change
    idea2 = SimpleNamespace(play_badge="VANNA-CHARM", strike=23700.0, quality_score=80, score_reason="")
    assert apply_active_short_magnet_penalty(idea2, {"active": False, "primary_strike": 23700}, 50) == 80
