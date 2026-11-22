"""Three configurable setup rules → candidate GO cards (pre-veto).

Parameters come from ``config/crypto.yaml`` under ``engine.setups``.
Starting numbers — not calibrated edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from crypto.config import load_config


@dataclass
class SetupCandidate:
    setup: str
    side: str
    entry_zone: list[float]
    target: float
    stop: float
    score: int
    reasons: list[str]
    invalidation: str


def _setup_params() -> dict[str, Any]:
    cfg = load_config()
    eng = cfg.get("engine") or {}
    return {
        "atr_pct": float(eng.get("atr_pct") or 0.004),
        "size_cap_usd": float(eng.get("size_cap_usd") or 2500),
        "card_valid_minutes": int(eng.get("card_valid_minutes") or 20),
        "model_version": str(eng.get("model_version") or "rules-v1"),
        "rules_version": str(eng.get("rules_version") or "setups-v1"),
        "setups": dict(eng.get("setups") or {}),
    }


def atr_proxy(spot: float, atr_pct: float | None = None) -> float:
    pct = atr_pct if atr_pct is not None else _setup_params()["atr_pct"]
    return float(spot) * float(pct)


def evaluate_setups(
    gex: dict[str, Any],
    orderflow: dict[str, Any],
    *,
    mark: Optional[float] = None,
) -> Optional[SetupCandidate]:
    """Return the first matching setup, or None.

    Evaluation order (configurable priority later): breakout → fade → flip.
    """
    params = _setup_params()
    setups_cfg = params["setups"]
    spot = float(gex["spot"])
    mark = float(mark if mark is not None else orderflow.get("mark") or spot)
    flip = float(gex["flip"]) if gex.get("flip") is not None else spot
    call_wall = gex.get("call_wall")
    put_wall = gex.get("put_wall")
    magnet = gex.get("magnet") or spot
    atr = atr_proxy(spot, params["atr_pct"])
    regime = gex.get("regime")
    agree = bool(gex.get("models_agree"))

    cvd_1m = float(orderflow.get("cvd_1m") or 0)
    cvd_5m = float(orderflow.get("cvd_5m") or 0)
    cvd_15m = float(orderflow.get("cvd_15m") or 0)
    imbalance = float(orderflow.get("book_imbalance") or 0)
    oi_delta = float(orderflow.get("oi_delta") or 0)

    # --- 1. Negative-gamma breakout ---
    ng = setups_cfg.get("negative_gamma_breakout") or {}
    if regime == "negative_gamma" and agree:
        cvd_min = float(ng.get("cvd_5m_min") or 50)
        need_confirm = bool(ng.get("require_imbalance_align", True))
        aligned = (not need_confirm) or (imbalance * cvd_5m > 0)
        oi_ok = (not bool(ng.get("require_oi_confirm", False))) or (
            oi_delta * cvd_5m > 0
        )
        if abs(cvd_5m) >= cvd_min and aligned and oi_ok:
            side = "buy" if cvd_5m > 0 else "sell"
            tgt_mult = float(ng.get("target_atr_mult") or 1.75)
            stop_mult = float(ng.get("stop_atr_mult") or 0.75)
            if side == "buy":
                target = float(call_wall) if call_wall else mark + tgt_mult * atr
                stop = flip if flip < mark else mark - stop_mult * atr
            else:
                target = float(put_wall) if put_wall else mark - tgt_mult * atr
                stop = flip if flip > mark else mark + stop_mult * atr
            return SetupCandidate(
                setup="negative_gamma_breakout",
                side=side,
                entry_zone=[mark, mark],
                target=target,
                stop=stop,
                score=int(ng.get("base_score") or 65),
                reasons=[
                    "Negative gamma regime — dealers amplify moves (convention+flow agree).",
                    f"Perp CVD 5m={cvd_5m:.1f} with book imbalance {imbalance:+.2f} confirming.",
                    "Target next wall; invalidate back through flip / ATR stop.",
                ],
                invalidation=f"Stop {stop:.2f} or regime disagreement",
            )

    # --- 2. Positive-gamma fade ---
    pg = setups_cfg.get("positive_gamma_fade") or {}
    if regime == "positive_gamma" and agree:
        stretch_mult = float(pg.get("stretch_atr_mult") or 1.5)
        stretch = abs(mark - float(magnet))
        # CVD diverging from stretch direction
        stretch_dir = 1.0 if mark > float(magnet) else -1.0
        cvd_div = cvd_15m * stretch_dir < 0
        between_walls = True
        if call_wall is not None and put_wall is not None:
            lo, hi = sorted([float(put_wall), float(call_wall)])
            between_walls = lo <= mark <= hi
        above_flip = mark >= flip
        if (
            stretch > stretch_mult * atr
            and cvd_div
            and between_walls
            and above_flip
        ):
            side = "sell" if mark > float(magnet) else "buy"
            entry_w = float(pg.get("entry_band_pct") or 0.001)
            if side == "sell":
                stop = (
                    float(call_wall)
                    if call_wall
                    else mark + float(pg.get("stop_atr_mult") or 1.0) * atr
                )
            else:
                stop = (
                    float(put_wall)
                    if put_wall
                    else mark - float(pg.get("stop_atr_mult") or 1.0) * atr
                )
            return SetupCandidate(
                setup="positive_gamma_fade",
                side=side,
                entry_zone=[mark * (1 - entry_w), mark * (1 + entry_w)],
                target=float(magnet),
                stop=stop,
                score=int(pg.get("base_score") or 68),
                reasons=[
                    "Convention and flow GEX agree positive around spot.",
                    f"Stretch {stretch / atr:.1f}× ATR from magnet with CVD 15m divergence.",
                    "Fade toward heaviest-gamma strike; stop beyond nearer wall.",
                ],
                invalidation=f"Stop {stop:.2f} or convention/flow regime disagreement",
            )

    # --- 3. Flip cross ---
    fc = setups_cfg.get("flip_cross") or {}
    if agree and regime in {"positive_gamma", "negative_gamma"}:
        # Spot crossed flip on rising |CVD|
        cross_band = float(fc.get("cross_band_pct") or 0.0015)
        vol_min = float(fc.get("cvd_1m_min") or 20)
        dist = (mark - flip) / spot if spot else 0.0
        crossed = abs(dist) <= cross_band * 3 and abs(cvd_1m) >= vol_min
        # Prefer clear side of flip matching CVD
        side_ok = (mark > flip and cvd_1m > 0) or (mark < flip and cvd_1m < 0)
        if crossed and side_ok:
            side = "buy" if cvd_1m > 0 else "sell"
            tgt_mult = float(fc.get("target_atr_mult") or 1.5)
            if side == "buy":
                target = float(call_wall) if call_wall else mark + tgt_mult * atr
            else:
                target = float(put_wall) if put_wall else mark - tgt_mult * atr
            return SetupCandidate(
                setup="flip_cross",
                side=side,
                entry_zone=[mark * 0.9995, mark * 1.0005],
                target=target,
                stop=flip,
                score=int(fc.get("base_score") or 62),
                reasons=[
                    f"Spot crossed flip ({flip:.0f}) with rising CVD 1m={cvd_1m:.1f}.",
                    f"Regime {regime} with convention/flow agreement.",
                    "Target next wall in the new regime; invalidate back through flip.",
                ],
                invalidation=f"Back through flip {flip:.2f}",
            )

    return None


def engine_versions() -> dict[str, str]:
    p = _setup_params()
    return {
        "model_version": p["model_version"],
        "rules_version": p["rules_version"],
    }
