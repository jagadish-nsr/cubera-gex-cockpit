"""CryptoCubera orderflow tape alerts — BTC / ETH / SOL only.

Triggered from perp CVD windows, book imbalance, OI Δ, and crypto GEX walls.
Decision-support alerts for the cockpit; never places orders.

This module is intentionally separate from the equity Cubera orderflow tape
package used for cash-index futures. Do not import that package here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from crypto.config import load_config
from crypto.providers.types import utcnow

# Crypto underlyings only — never NIFTY / BANKNIFTY / SENSEX.
CRYPTO_UNDERLYINGS = frozenset({"BTC", "ETH", "SOL"})

# Crypto-native alert types (names avoid equity Sentinel branding).
TYPE_CVD_IMPULSE_LONG = "CVD_IMPULSE_LONG"
TYPE_CVD_IMPULSE_SHORT = "CVD_IMPULSE_SHORT"
TYPE_CVD_DIVERGENCE_LONG = "CVD_DIVERGENCE_LONG"
TYPE_CVD_DIVERGENCE_SHORT = "CVD_DIVERGENCE_SHORT"
TYPE_WALL_ABSORPTION_LONG = "WALL_ABSORPTION_LONG"
TYPE_WALL_ABSORPTION_SHORT = "WALL_ABSORPTION_SHORT"
TYPE_TAPE_EXHAUSTION_UP = "TAPE_EXHAUSTION_UP"
TYPE_TAPE_EXHAUSTION_DOWN = "TAPE_EXHAUSTION_DOWN"

BULLISH_TYPES = frozenset(
    {
        TYPE_CVD_IMPULSE_LONG,
        TYPE_CVD_DIVERGENCE_LONG,
        TYPE_WALL_ABSORPTION_LONG,
        TYPE_TAPE_EXHAUSTION_DOWN,  # selling climax → bounce watch
    }
)
BEARISH_TYPES = frozenset(
    {
        TYPE_CVD_IMPULSE_SHORT,
        TYPE_CVD_DIVERGENCE_SHORT,
        TYPE_WALL_ABSORPTION_SHORT,
        TYPE_TAPE_EXHAUSTION_UP,
    }
)


@dataclass
class FlowAlert:
    """One crypto orderflow alert for the cockpit tape."""

    alert_id: str
    type: str
    underlying: str
    side: str  # long | short | neutral
    score: int
    title: str
    detail: str
    mark: float
    invalidation: Optional[float] = None
    drivers: list[str] = field(default_factory=list)
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "type": self.type,
            "underlying": self.underlying,
            "side": self.side,
            "score": int(self.score),
            "title": self.title,
            "detail": self.detail,
            "mark": float(self.mark),
            "invalidation": self.invalidation,
            "drivers": list(self.drivers),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "bullish": self.type in BULLISH_TYPES,
            "bearish": self.type in BEARISH_TYPES,
            "desk": "CryptoCubera",
            "scope": "crypto",  # never equity indices
        }


@dataclass
class FlowAlertState:
    """Per-asset cooldown so the same type does not spam every poll."""

    last_fired: dict[str, datetime] = field(default_factory=dict)

    def allow(self, alert_type: str, now: datetime, cooldown_s: float) -> bool:
        prev = self.last_fired.get(alert_type)
        if prev is None:
            return True
        age = (now - prev).total_seconds()
        return age >= float(cooldown_s)

    def mark(self, alert_type: str, now: datetime) -> None:
        self.last_fired[alert_type] = now


def _params() -> dict[str, Any]:
    cfg = load_config()
    eng = cfg.get("engine") or {}
    fa = dict(eng.get("flow_alerts") or {})
    return {
        "cvd_impulse_5m_min": float(fa.get("cvd_impulse_5m_min") or 80),
        "cvd_impulse_1m_min": float(fa.get("cvd_impulse_1m_min") or 15),
        "require_imbalance_align": bool(fa.get("require_imbalance_align", True)),
        "divergence_15m_min": float(fa.get("divergence_15m_min") or 40),
        "divergence_1m_min": float(fa.get("divergence_1m_min") or 10),
        "wall_band_pct": float(fa.get("wall_band_pct") or 0.004),
        "exhaustion_1m_min": float(fa.get("exhaustion_1m_min") or 50),
        "exhaustion_15m_oppose_min": float(fa.get("exhaustion_15m_oppose_min") or 20),
        "min_score": int(fa.get("min_score") or 60),
        "cooldown_s": float(fa.get("cooldown_s") or 90),
        "max_alerts": int(fa.get("max_alerts") or 6),
    }


def _near_wall(spot: float, wall: float | None, band_pct: float) -> bool:
    if wall is None:
        return False
    w = float(wall)
    if w <= 0 or spot <= 0:
        return False
    return abs(spot - w) / spot <= band_pct


def evaluate_flow_alerts(
    gex: dict[str, Any],
    orderflow: dict[str, Any],
    *,
    underlying: str,
    state: FlowAlertState | None = None,
    now: datetime | None = None,
    apply_cooldown: bool = True,
) -> list[dict[str, Any]]:
    """Classify crypto orderflow alerts for the active underlying.

    Returns serialized alert dicts, newest-first by score. Empty when the
    underlying is not a crypto desk asset.
    """
    u = (underlying or "").strip().upper()
    if u not in CRYPTO_UNDERLYINGS:
        return []

    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    params = _params()
    state = state or FlowAlertState()

    mark = float(orderflow.get("mark") or gex.get("spot") or 0)
    spot = float(gex.get("spot") or mark)
    if mark <= 0:
        return []

    cvd_1m = float(orderflow.get("cvd_1m") or 0)
    cvd_5m = float(orderflow.get("cvd_5m") or 0)
    cvd_15m = float(orderflow.get("cvd_15m") or 0)
    imbalance = float(orderflow.get("book_imbalance") or 0)
    oi_delta = float(orderflow.get("oi_delta") or 0)
    call_wall = gex.get("call_wall")
    put_wall = gex.get("put_wall")
    flip = gex.get("flip")

    candidates: list[FlowAlert] = []

    # 1) CVD impulse — short-horizon tape pressure aligned with book
    if abs(cvd_5m) >= params["cvd_impulse_5m_min"] and abs(cvd_1m) >= params[
        "cvd_impulse_1m_min"
    ]:
        aligned = (not params["require_imbalance_align"]) or (imbalance * cvd_5m > 0)
        same_sign = cvd_1m * cvd_5m > 0
        if aligned and same_sign:
            long = cvd_5m > 0
            typ = TYPE_CVD_IMPULSE_LONG if long else TYPE_CVD_IMPULSE_SHORT
            score = min(
                95,
                params["min_score"]
                + int(min(25, abs(cvd_5m) / 20))
                + (5 if oi_delta * cvd_5m > 0 else 0),
            )
            candidates.append(
                FlowAlert(
                    alert_id=f"flow-{uuid4().hex[:10]}",
                    type=typ,
                    underlying=u,
                    side="long" if long else "short",
                    score=score,
                    title=f"{u} CVD impulse {'long' if long else 'short'}",
                    detail=(
                        f"5m CVD {cvd_5m:+.1f} with 1m {cvd_1m:+.1f}; "
                        f"book imbalance {imbalance:+.2f}."
                    ),
                    mark=mark,
                    invalidation=float(flip) if flip is not None else None,
                    drivers=[
                        f"cvd_5m={cvd_5m:+.1f}",
                        f"cvd_1m={cvd_1m:+.1f}",
                        f"imbalance={imbalance:+.2f}",
                    ],
                    created_at=now,
                )
            )

    # 2) CVD divergence — longer window vs short window (fade watch)
    if abs(cvd_15m) >= params["divergence_15m_min"] and abs(cvd_1m) >= params[
        "divergence_1m_min"
    ]:
        if cvd_15m * cvd_1m < 0:
            # Short CVD fading against longer sell pressure → long divergence
            long = cvd_1m > 0 and cvd_15m < 0
            typ = TYPE_CVD_DIVERGENCE_LONG if long else TYPE_CVD_DIVERGENCE_SHORT
            score = min(90, params["min_score"] + 8 + int(min(15, abs(cvd_15m) / 30)))
            candidates.append(
                FlowAlert(
                    alert_id=f"flow-{uuid4().hex[:10]}",
                    type=typ,
                    underlying=u,
                    side="long" if long else "short",
                    score=score,
                    title=f"{u} CVD divergence {'long' if long else 'short'}",
                    detail=(
                        f"15m CVD {cvd_15m:+.1f} vs 1m {cvd_1m:+.1f} — "
                        "short-horizon tape fading the longer window."
                    ),
                    mark=mark,
                    invalidation=float(flip) if flip is not None else None,
                    drivers=[
                        f"cvd_15m={cvd_15m:+.1f}",
                        f"cvd_1m={cvd_1m:+.1f}",
                    ],
                    created_at=now,
                )
            )

    # 3) Wall absorption — CVD opposing stretch into a named crypto GEX wall
    band = params["wall_band_pct"]
    if _near_wall(spot, call_wall, band) and cvd_5m < -params["cvd_impulse_1m_min"]:
        score = min(92, params["min_score"] + 12)
        candidates.append(
            FlowAlert(
                alert_id=f"flow-{uuid4().hex[:10]}",
                type=TYPE_WALL_ABSORPTION_SHORT,
                underlying=u,
                side="short",
                score=score,
                title=f"{u} absorption at call wall",
                detail=(
                    f"Spot near call wall {float(call_wall):.0f}; "
                    f"CVD 5m {cvd_5m:+.1f} (sellers into resistance)."
                ),
                mark=mark,
                invalidation=float(call_wall) * 1.002 if call_wall else None,
                drivers=[
                    f"call_wall={float(call_wall):.0f}",
                    f"cvd_5m={cvd_5m:+.1f}",
                ],
                created_at=now,
            )
        )
    if _near_wall(spot, put_wall, band) and cvd_5m > params["cvd_impulse_1m_min"]:
        score = min(92, params["min_score"] + 12)
        candidates.append(
            FlowAlert(
                alert_id=f"flow-{uuid4().hex[:10]}",
                type=TYPE_WALL_ABSORPTION_LONG,
                underlying=u,
                side="long",
                score=score,
                title=f"{u} absorption at put wall",
                detail=(
                    f"Spot near put wall {float(put_wall):.0f}; "
                    f"CVD 5m {cvd_5m:+.1f} (buyers into support)."
                ),
                mark=mark,
                invalidation=float(put_wall) * 0.998 if put_wall else None,
                drivers=[
                    f"put_wall={float(put_wall):.0f}",
                    f"cvd_5m={cvd_5m:+.1f}",
                ],
                created_at=now,
            )
        )

    # 4) Tape exhaustion — 1m climax opposing the 15m trend
    if abs(cvd_1m) >= params["exhaustion_1m_min"] and abs(cvd_15m) >= params[
        "exhaustion_15m_oppose_min"
    ]:
        if cvd_1m > 0 and cvd_15m < 0:
            candidates.append(
                FlowAlert(
                    alert_id=f"flow-{uuid4().hex[:10]}",
                    type=TYPE_TAPE_EXHAUSTION_UP,
                    underlying=u,
                    side="short",
                    score=min(88, params["min_score"] + 10),
                    title=f"{u} tape exhaustion up",
                    detail=(
                        f"1m buy climax {cvd_1m:+.1f} against 15m sell "
                        f"{cvd_15m:+.1f} — watch for rejection."
                    ),
                    mark=mark,
                    invalidation=mark * 1.004,
                    drivers=[f"cvd_1m={cvd_1m:+.1f}", f"cvd_15m={cvd_15m:+.1f}"],
                    created_at=now,
                )
            )
        elif cvd_1m < 0 and cvd_15m > 0:
            candidates.append(
                FlowAlert(
                    alert_id=f"flow-{uuid4().hex[:10]}",
                    type=TYPE_TAPE_EXHAUSTION_DOWN,
                    underlying=u,
                    side="long",
                    score=min(88, params["min_score"] + 10),
                    title=f"{u} tape exhaustion down",
                    detail=(
                        f"1m sell climax {cvd_1m:+.1f} against 15m buy "
                        f"{cvd_15m:+.1f} — watch for bounce."
                    ),
                    mark=mark,
                    invalidation=mark * 0.996,
                    drivers=[f"cvd_1m={cvd_1m:+.1f}", f"cvd_15m={cvd_15m:+.1f}"],
                    created_at=now,
                )
            )

    # Cooldown filter
    cooled: list[FlowAlert] = []
    for alert in candidates:
        if alert.score < params["min_score"]:
            continue
        if apply_cooldown and not state.allow(
            alert.type, now, params["cooldown_s"]
        ):
            continue
        cooled.append(alert)
        if apply_cooldown:
            state.mark(alert.type, now)

    cooled.sort(key=lambda a: (-a.score, a.type))
    return [a.to_dict() for a in cooled[: params["max_alerts"]]]
