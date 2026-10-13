"""commentary/bites/gating.py — Strict Risk, Mode & Calibration Gating for Suggestion Bites."""

from __future__ import annotations

from typing import Any
from commentary.models import SuggestionBite
from commentary.config_loader import get_commentary_config


def check_risk_gating(
    bite: SuggestionBite,
    capital: float | None = None,
    max_risk_pct: float | None = None,
) -> tuple[bool, list[str]]:
    """Enforce user risk profile, capital bounds, and minimum R:R constraints.

    Returns:
        (passed_gating, list_of_reasons)
    """
    reasons: list[str] = []
    cfg = get_commentary_config()
    profile = cfg.get("profile", {})
    cap = capital if capital is not None else float(profile.get("capital", 500000.0))
    pct = max_risk_pct if max_risk_pct is not None else float(profile.get("max_risk_pct", 1.5))
    max_allowed_loss_inr = cap * (pct / 100.0)

    # 1. Capital risk limit
    max_loss = float(bite.risk.get("max_loss_per_lot", 0.0))
    if max_loss > max_allowed_loss_inr:
        reasons.append(
            f"Risk per lot (₹{max_loss:,.0f}) exceeds maximum allowed risk "
            f"(₹{max_allowed_loss_inr:,.0f} = {pct}% of ₹{cap:,.0f})"
        )

    # 2. Risk:Reward constraint (only for directional actionable bites)
    if bite.type in ("ACT-IF", "PREPARE"):
        rr = float(bite.risk.get("rr", 0.0))
        if rr < 1.2:
            reasons.append(f"R:R ratio ({rr:.2f}) is below minimum acceptable threshold 1.2")

    # 3. Structure constraint
    allowed_structures = profile.get("allowed_structures", "defined_risk_only")
    if allowed_structures == "defined_risk_only":
        action = bite.action_frame.lower()
        if "naked" in action or "short call" in action or "short put" in action:
            reasons.append("Naked short options prohibited by user risk profile (defined-risk only)")

    return len(reasons) == 0, reasons


def apply_mode_and_calibration_gating(
    bite: SuggestionBite,
    is_calibrated: bool = False,
    session_count: int = 0,
) -> SuggestionBite:
    """Enforce shadow mode baseline and calibration downgrade policy.

    If session count < min_shadow_sessions (20), force mode: "shadow".
    If uncalibrated and actionable bites are not permitted when uncalibrated,
    downgrades ACT-IF to WATCH or PREPARE.
    """
    cfg = get_commentary_config()
    bites_cfg = cfg.get("bites", {})

    min_shadow = int(bites_cfg.get("min_shadow_sessions_for_promotion", 20))
    cfg_mode = bites_cfg.get("mode", "shadow")

    # Force shadow mode if under evaluation period or configured as shadow
    mode = "shadow" if (session_count < min_shadow or cfg_mode == "shadow") else "live"

    # Calibration check
    allow_uncalibrated = bool(bites_cfg.get("allow_actionable_when_uncalibrated", False))
    if not is_calibrated and not allow_uncalibrated:
        if bite.type == "ACT-IF":
            # Downgrade to PREPARE
            bite.type = "PREPARE"
            bite.headline = f"[Advisory] {bite.headline}"

    bite.badge["mode"] = mode
    bite.badge["calibrated"] = is_calibrated
    bite.badge["session_count"] = session_count

    return bite
