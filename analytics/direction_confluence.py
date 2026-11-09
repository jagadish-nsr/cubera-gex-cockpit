"""Direction confluence board — every listed signal casts an UP/DOWN vote.

Fork B (user-selected): GEX regime and PCR vote as direction leans, using the
India retail mapping (high PCR → bullish; POSITIVE/call-heavy book → UP lean).

This is a confluence *lean board*, not a predictor. The cockpit must keep that
framing in the UI copy. Human confirms.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


PCR_UP = 1.25
PCR_DOWN = 0.85
BIAS_MILD = 0.4
SKEW_THR = 0.015
VIX_CALM = 12.0
VIX_STRESS = 22.0
BREADTH_UP = 1.2
BREADTH_DOWN = 0.833  # ~1/1.2
MAX_PAIN_BAND_PTS = 25.0


@dataclass
class ConfluenceCell:
    key: str
    label: str
    lean: str          # UP | DOWN | FLAT | NA
    vote: int          # +1 | 0 | -1
    strength: float    # 0..1 colour intensity
    detail: str


@dataclass
class ConfluenceResult:
    cells: list[ConfluenceCell] = field(default_factory=list)
    up_count: int = 0
    down_count: int = 0
    flat_count: int = 0
    na_count: int = 0
    net_votes: int = 0
    verdict: str = "NO READ"
    color: str = "var(--spark)"
    headline: str = ""
    agreement_pct: float = 0.0


def _cell(key: str, label: str, lean: str, vote: int, strength: float, detail: str) -> ConfluenceCell:
    return ConfluenceCell(
        key=key,
        label=label,
        lean=lean,
        vote=int(vote),
        strength=round(max(0.0, min(1.0, float(strength))), 3),
        detail=detail,
    )


def _na(key: str, label: str, detail: str) -> ConfluenceCell:
    return _cell(key, label, "NA", 0, 0.0, detail)


def _pcr_cell(pcr: Optional[float]) -> ConfluenceCell:
    if pcr is None or pcr != pcr or pcr <= 0:
        return _na("pcr", "PCR (OI)", "PCR unavailable")
    # India convention: put-heavy OI → bullish lean; call-heavy → bearish lean.
    if pcr >= PCR_UP:
        strength = min(1.0, (pcr - 1.0) / 0.75)
        return _cell("pcr", "PCR (OI)", "UP", 1, strength, f"PCR {pcr:.2f} put-heavy OI → UP lean")
    if pcr <= PCR_DOWN:
        strength = min(1.0, (1.0 - pcr) / 0.4)
        return _cell("pcr", "PCR (OI)", "DOWN", -1, strength, f"PCR {pcr:.2f} call-heavy OI → DOWN lean")
    mid = (pcr - 1.0) / max(PCR_UP - 1.0, 1e-9) if pcr >= 1.0 else (1.0 - pcr) / max(1.0 - PCR_DOWN, 1e-9)
    return _cell("pcr", "PCR (OI)", "FLAT", 0, abs(mid) * 0.35, f"PCR {pcr:.2f} mixed OI")


def _regime_cell(regime: Optional[str]) -> ConfluenceCell:
    r = str(regime or "").strip().upper()
    if not r or r in ("NEUTRAL", "FLAT", "UNKNOWN"):
        if r in ("NEUTRAL", "FLAT"):
            return _cell("gex_regime", "GEX regime", "FLAT", 0, 0.2, f"Regime {r or 'FLAT'}")
        return _na("gex_regime", "GEX regime", "Regime unavailable")
    if r == "POSITIVE":
        return _cell("gex_regime", "GEX regime", "UP", 1, 0.85, "POSITIVE call-heavy book → UP lean")
    if r == "NEGATIVE":
        return _cell("gex_regime", "GEX regime", "DOWN", -1, 0.85, "NEGATIVE put-heavy book → DOWN lean")
    return _na("gex_regime", "GEX regime", f"Regime {r}")


def _bias_cell(score: Optional[float]) -> ConfluenceCell:
    if score is None or score != score:
        return _na("directional_bias", "Directional bias", "Bias unavailable")
    detail = f"IOAS score {score:+.2f}"
    if score >= BIAS_MILD:
        return _cell("directional_bias", "Directional bias", "UP", 1, min(1.0, abs(score) / 2.5), detail)
    if score <= -BIAS_MILD:
        return _cell("directional_bias", "Directional bias", "DOWN", -1, min(1.0, abs(score) / 2.5), detail)
    return _cell("directional_bias", "Directional bias", "FLAT", 0, abs(score) / BIAS_MILD * 0.4, detail)


def _flip_cell(spot: Optional[float], flip: Optional[float], in_positive_zone: Optional[bool]) -> ConfluenceCell:
    if in_positive_zone is True:
        dist = (float(spot) - float(flip)) if spot and flip else 0.0
        return _cell("spot_vs_flip", "Spot vs flip", "UP", 1, min(1.0, abs(dist) / 100.0 + 0.4),
                     f"Spot on call side of balance ({dist:+.0f} pts)")
    if in_positive_zone is False:
        dist = (float(flip) - float(spot)) if spot and flip else 0.0
        return _cell("spot_vs_flip", "Spot vs flip", "DOWN", -1, min(1.0, abs(dist) / 100.0 + 0.4),
                     f"Spot on put side of balance ({dist:+.0f} pts to flip)")
    return _na("spot_vs_flip", "Spot vs flip", "No flip crossing in range")


def _wall_mid_cell(spot: Optional[float], call_wall: Optional[float], put_wall: Optional[float]) -> ConfluenceCell:
    if not spot or not call_wall or not put_wall:
        return _na("wall_mid", "Wall midpoint", "Walls unavailable")
    mid = (float(call_wall) + float(put_wall)) / 2.0
    dist = float(spot) - mid
    span = max(abs(float(call_wall) - float(put_wall)) / 2.0, 50.0)
    if abs(dist) < span * 0.08:
        return _cell("wall_mid", "Wall midpoint", "FLAT", 0, 0.25, f"Spot near wall mid {mid:,.0f}")
    if dist > 0:
        return _cell("wall_mid", "Wall midpoint", "UP", 1, min(1.0, abs(dist) / span),
                     f"Spot {dist:+.0f} above wall mid {mid:,.0f}")
    return _cell("wall_mid", "Wall midpoint", "DOWN", -1, min(1.0, abs(dist) / span),
                 f"Spot {dist:+.0f} below wall mid {mid:,.0f}")


def _skew_cell(skew_25d: Optional[float]) -> ConfluenceCell:
    if skew_25d is None or skew_25d != skew_25d:
        return _na("iv_skew", "IV skew", "25Δ skew unavailable")
    detail = f"25Δ skew {skew_25d * 100:+.1f}%"
    if skew_25d > SKEW_THR:
        return _cell("iv_skew", "IV skew", "UP", 1, min(1.0, skew_25d / (SKEW_THR * 2)), detail + " put premium / call demand")
    if skew_25d < -SKEW_THR:
        return _cell("iv_skew", "IV skew", "DOWN", -1, min(1.0, abs(skew_25d) / (SKEW_THR * 2)), detail + " call premium / fear")
    return _cell("iv_skew", "IV skew", "FLAT", 0, abs(skew_25d) / SKEW_THR * 0.4, detail)


def _vix_cell(india_vix: Any) -> ConfluenceCell:
    vix_val = None
    if isinstance(india_vix, dict):
        vix_val = india_vix.get("last")
    elif isinstance(india_vix, (int, float)):
        vix_val = float(india_vix)
    try:
        vix_val = float(vix_val) if vix_val is not None else None
    except (TypeError, ValueError):
        vix_val = None
    if vix_val is None or vix_val != vix_val:
        return _na("india_vix", "India VIX", "VIX unavailable")
    detail = f"India VIX {vix_val:.1f}"
    if vix_val < VIX_CALM:
        return _cell("india_vix", "India VIX", "UP", 1, min(1.0, (VIX_CALM - vix_val) / 4.0 + 0.5), detail + " calm")
    if vix_val > VIX_STRESS:
        return _cell("india_vix", "India VIX", "DOWN", -1, min(1.0, (vix_val - VIX_STRESS) / 8.0 + 0.5), detail + " stressed")
    # Mid-range: map 12→+soft, 22→−soft but FLAT vote inside band.
    signed = -(vix_val - 17.0) / 5.0
    return _cell("india_vix", "India VIX", "FLAT", 0, abs(signed) * 0.35, detail + " mid-range")


def _breadth_cell(advances: Optional[int], declines: Optional[int], ratio: Optional[float]) -> ConfluenceCell:
    if advances is None or declines is None:
        return _na("breadth", "Market breadth", "Breadth unavailable")
    if ratio is None and declines:
        ratio = float(advances) / float(declines) if declines else None
    elif ratio is None and advances and not declines:
        ratio = 99.0
    detail = f"{advances} adv / {declines} dec"
    if ratio is None:
        return _cell("breadth", "Market breadth", "FLAT", 0, 0.2, detail)
    detail = f"{detail} ({ratio:.2f}x)"
    if ratio >= BREADTH_UP:
        return _cell("breadth", "Market breadth", "UP", 1, min(1.0, (ratio - 1.0) / 1.5 + 0.4), detail)
    if ratio <= BREADTH_DOWN:
        return _cell("breadth", "Market breadth", "DOWN", -1, min(1.0, (1.0 / max(ratio, 0.05) - 1.0) / 1.5 + 0.4), detail)
    return _cell("breadth", "Market breadth", "FLAT", 0, abs(ratio - 1.0) * 0.5, detail)


def _max_pain_cell(spot: Optional[float], max_pain: Optional[float]) -> ConfluenceCell:
    if not spot or not max_pain:
        return _na("max_pain", "Max pain", "Max pain unavailable")
    dist = float(max_pain) - float(spot)
    detail = f"Max pain {max_pain:,.0f} · {dist:+.0f} pts"
    if abs(dist) < MAX_PAIN_BAND_PTS:
        return _cell("max_pain", "Max pain", "FLAT", 0, 0.2, detail + " near settlement")
    # Below max pain → UP lean (settlement above); above → DOWN lean.
    if dist > 0:
        return _cell("max_pain", "Max pain", "UP", 1, min(1.0, abs(dist) / 150.0 + 0.35), detail + " spot below")
    return _cell("max_pain", "Max pain", "DOWN", -1, min(1.0, abs(dist) / 150.0 + 0.35), detail + " spot above")


def _positioning_cell(
    overnight_state: Optional[str],
    live_label: Optional[str],
) -> ConfluenceCell:
    state = str(overnight_state or "").strip().lower()
    live = str(live_label or "").strip().lower()
    up_tokens = ("accumulation", "long build-up", "long buildup", "short covering")
    down_tokens = ("distribution", "short build-up", "short buildup", "long unwinding")

    vote = 0
    bits = []
    if state in up_tokens or any(t in state for t in ("accumulation",)):
        vote += 1
        bits.append(f"overnight {state}")
    elif state in down_tokens or any(t in state for t in ("distribution",)):
        vote -= 1
        bits.append(f"overnight {state}")
    if live in up_tokens or "long build" in live or live == "short covering":
        vote += 1
        bits.append(f"live {live}")
    elif live in down_tokens or "short build" in live or live == "long unwinding":
        vote -= 1
        bits.append(f"live {live}")

    if not bits:
        return _na("positioning", "Futures positioning", "No overnight/live build-up read")
    detail = "; ".join(bits)
    if vote > 0:
        return _cell("positioning", "Futures positioning", "UP", 1, 0.55 + 0.2 * min(vote, 2), detail)
    if vote < 0:
        return _cell("positioning", "Futures positioning", "DOWN", -1, 0.55 + 0.2 * min(abs(vote), 2), detail)
    return _cell("positioning", "Futures positioning", "FLAT", 0, 0.3, detail + " · mixed clocks")


def _verdict(up: int, down: int, flat: int) -> tuple[str, str, str, float]:
    voting = up + down
    if voting == 0:
        return "NO READ", "var(--spark)", "No directional votes yet — waiting on live inputs.", 0.0
    net = up - down
    agree = max(up, down) / voting
    if abs(net) >= 2 and agree >= 0.6:
        if net > 0:
            return (
                "LEAN UP",
                "var(--go)",
                f"UP confluence {up}/{voting} voting · {flat} flat. Lean board — not a predictor.",
                round(agree * 100.0, 1),
            )
        return (
            "LEAN DOWN",
            "var(--stop)",
            f"DOWN confluence {down}/{voting} voting · {flat} flat. Lean board — not a predictor.",
            round(agree * 100.0, 1),
        )
    if net > 0:
        return (
            "MIXED UP",
            "var(--go)",
            f"Soft UP {up} vs {down} DOWN · {flat} flat. No hard confluence.",
            round(agree * 100.0, 1),
        )
    if net < 0:
        return (
            "MIXED DOWN",
            "var(--stop)",
            f"Soft DOWN {down} vs {up} UP · {flat} flat. No hard confluence.",
            round(agree * 100.0, 1),
        )
    return (
        "MIXED",
        "var(--spark)",
        f"Split board {up} UP / {down} DOWN · {flat} flat. Stay small.",
        round(agree * 100.0, 1),
    )


def compute_direction_confluence(
    *,
    spot: Optional[float] = None,
    call_wall: Optional[float] = None,
    put_wall: Optional[float] = None,
    flip: Optional[float] = None,
    in_positive_zone: Optional[bool] = None,
    regime: Optional[str] = None,
    pcr: Optional[float] = None,
    bias_score: Optional[float] = None,
    max_pain: Optional[float] = None,
    skew_25d: Optional[float] = None,
    india_vix: Any = None,
    advances: Optional[int] = None,
    declines: Optional[int] = None,
    breadth_ratio: Optional[float] = None,
    overnight_state: Optional[str] = None,
    live_label: Optional[str] = None,
) -> ConfluenceResult:
    """Build the all-signals-vote confluence heatmap payload."""
    cells = [
        _bias_cell(bias_score),
        _regime_cell(regime),
        _pcr_cell(pcr),
        _flip_cell(spot, flip, in_positive_zone),
        _wall_mid_cell(spot, call_wall, put_wall),
        _skew_cell(skew_25d),
        _vix_cell(india_vix),
        _breadth_cell(advances, declines, breadth_ratio),
        _max_pain_cell(spot, max_pain),
        _positioning_cell(overnight_state, live_label),
    ]

    up = sum(1 for c in cells if c.lean == "UP")
    down = sum(1 for c in cells if c.lean == "DOWN")
    flat = sum(1 for c in cells if c.lean == "FLAT")
    na = sum(1 for c in cells if c.lean == "NA")
    verdict, color, headline, agree = _verdict(up, down, flat)
    return ConfluenceResult(
        cells=cells,
        up_count=up,
        down_count=down,
        flat_count=flat,
        na_count=na,
        net_votes=up - down,
        verdict=verdict,
        color=color,
        headline=headline,
        agreement_pct=agree,
    )


def confluence_to_dict(result: ConfluenceResult) -> dict[str, Any]:
    return {
        "verdict": result.verdict,
        "color": result.color,
        "headline": result.headline,
        "up_count": result.up_count,
        "down_count": result.down_count,
        "flat_count": result.flat_count,
        "na_count": result.na_count,
        "net_votes": result.net_votes,
        "agreement_pct": result.agreement_pct,
        "cells": [
            {
                "key": c.key,
                "label": c.label,
                "lean": c.lean,
                "vote": c.vote,
                "strength": c.strength,
                "detail": c.detail,
            }
            for c in result.cells
        ],
    }
