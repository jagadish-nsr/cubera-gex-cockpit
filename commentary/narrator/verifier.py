"""commentary/narrator/verifier.py — Strict Fact & Direction Verifier for Commentary prose."""

from __future__ import annotations

import re
from typing import Any
from commentary.config_loader import get_commentary_config


DEFAULT_FORBIDDEN = [
    "sure shot",
    "guaranteed",
    "can't lose",
    "cant lose",
    "buy now",
    "sell now",
    "risk-free",
    "risk free",
    "easy money",
    "hurry",
    "last chance",
    "to the moon",
    "free money",
    "blind buy",
]

# Words that contradict positive price action
NEGATIVE_DIRECTION_TERMS = ["crashed", "collapsed", "slumped", "cratered", "fell sharply", "tumbled"]
# Words that contradict negative price action
POSITIVE_DIRECTION_TERMS = ["skyrocketed", "surged", "rallied strongly", "rallied sharply", "rocketed"]


def _extract_all_numbers_from_dict(d: Any) -> list[float]:
    """Recursively harvest all numeric values from facts dictionary."""
    numbers: list[float] = []
    if isinstance(d, dict):
        for v in d.values():
            numbers.extend(_extract_all_numbers_from_dict(v))
    elif isinstance(d, (list, tuple, set)):
        for item in d:
            numbers.extend(_extract_all_numbers_from_dict(item))
    elif isinstance(d, (int, float)) and not isinstance(d, bool):
        numbers.append(float(d))
    return numbers


def _extract_numbers_from_text(text: str) -> list[float]:
    """Extract standalone numeric tokens from text, removing commas and currency symbols."""
    # Matches integers, decimals, optionally signed, e.g. 24,500, +45.2, -120, 0.35%
    # Ignores trailing 'm' or 'k' or '%'
    tokens = re.findall(r"(?:[+-]|\b)\d+(?:,\d{3})*(?:\.\d+)?", text)
    nums: list[float] = []
    for t in tokens:
        clean = t.replace(",", "")
        try:
            val = float(clean)
            nums.append(val)
        except ValueError:
            pass
    return nums


def _matches_any_fact(extracted: float, fact_numbers: list[float]) -> bool:
    """Check if an extracted number is matched by any number in facts within reasonable tolerance."""
    for fn in fact_numbers:
        # Exact or close match
        if abs(extracted - fn) < 1e-4:
            return True
        # If fact is in decimal (e.g. 0.005) and text is in percent (0.5)
        if abs(extracted - (fn * 100.0)) < 1e-2:
            return True
        if abs((extracted * 100.0) - fn) < 1e-2:
            return True
        # Integer rounding tolerance (e.g. 24510 vs 24510.3)
        if abs(extracted - fn) <= 1.0:
            return True
        # Relative tolerance within 0.5% (for rounded numbers in prose)
        if fn != 0 and abs(extracted - fn) / abs(fn) < 0.005:
            return True
    return False


def verify_commentary(
    text: str,
    facts: dict[str, Any],
    spot_delta: float | None = None,
    custom_forbidden: list[str] | None = None,
    strict_numeric: bool = False,
) -> tuple[bool, list[str]]:
    """Verify commentary prose for zero hallucinations, banned phrases, and directional consistency.

    Args:
        text: Generated headline and/or body.
        facts: Source facts dictionary for the event.
        spot_delta: Optional spot price change (positive = up, negative = down).
        custom_forbidden: Optional additional forbidden phrases.
        strict_numeric: If True, all extracted numbers must match a fact number.

    Returns:
        (is_valid, list_of_violations)
    """
    violations: list[str] = []
    text_lower = text.lower()

    # 1. Forbidden phrase check
    cfg = get_commentary_config()
    banned_list = custom_forbidden or cfg.get("bites", {}).get("forbidden_phrases", DEFAULT_FORBIDDEN)
    for phrase in banned_list:
        if phrase.lower() in text_lower:
            violations.append(f"Forbidden phrase detected: '{phrase}'")

    # 2. Directional contradiction check
    if spot_delta is not None:
        if spot_delta > 10.0:  # clearly up
            for term in NEGATIVE_DIRECTION_TERMS:
                if term in text_lower:
                    violations.append(f"Directional mismatch: positive delta ({spot_delta:+.1f}) with negative term '{term}'")
        elif spot_delta < -10.0:  # clearly down
            for term in POSITIVE_DIRECTION_TERMS:
                if term in text_lower:
                    violations.append(f"Directional mismatch: negative delta ({spot_delta:+.1f}) with positive term '{term}'")

    # 3. Numeric hallucination check (if strict_numeric is enabled)
    if strict_numeric:
        fact_nums = _extract_all_numbers_from_dict(facts)
        # Add basic time/constant numbers commonly allowed (e.g. 15, 0, 100, 1, 2)
        fact_nums.extend([0.0, 1.0, 2.0, 3.0, 15.0, 50.0, 100.0])
        extracted_nums = _extract_numbers_from_text(text)
        for num in extracted_nums:
            if not _matches_any_fact(num, fact_nums):
                violations.append(f"Unverified number in commentary: {num}")

    return len(violations) == 0, violations
