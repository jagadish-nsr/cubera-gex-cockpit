"""Hard vetoes above setups and inference.

No probability or score can override these. Stub hooks for risk / news /
venue stay false until wired; when raised they force NO-GO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from crypto.config import load_config
from crypto.feeds.stale import STALE_THRESHOLD_S, age_seconds, is_stale


@dataclass
class VetoContext:
    """Inputs for hard-veto evaluation."""

    now: datetime
    gex: dict[str, Any]
    chain_fetched_at: Optional[datetime] = None
    perp_fetched_at: Optional[datetime] = None
    live_signal_path: bool = False
    news_blackout: bool = False
    # Stub hooks — keep False until risk/news/venue services are real
    risk_daily_loss_hit: bool = False
    risk_margin_cap_hit: bool = False
    venue_not_operational: bool = False
    scheduled_event_within_30m: bool = False
    mock_loose_stale_s: float = 30.0


@dataclass
class VetoResult:
    vetoes: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.vetoes)


def _stale_threshold_s() -> float:
    cfg = load_config()
    feeds = cfg.get("feeds") or {}
    eng = (cfg.get("engine") or {}).get("vetoes") or {}
    return float(
        eng.get("stale_threshold_s")
        or feeds.get("stale_threshold_s")
        or STALE_THRESHOLD_S
    )


def evaluate_vetoes(ctx: VetoContext) -> VetoResult:
    vetoes: list[str] = []
    thr = _stale_threshold_s()

    if not ctx.gex.get("models_agree", False):
        vetoes.append("convention/flow GEX regime disagreement")

    if ctx.gex.get("regime") == "uncertain":
        vetoes.append("regime uncertain")

    if ctx.live_signal_path:
        if ctx.chain_fetched_at is not None and is_stale(
            ctx.chain_fetched_at, threshold_s=thr, now=ctx.now
        ):
            age = age_seconds(ctx.chain_fetched_at, now=ctx.now)
            vetoes.append(f"chain data stale ({age:.1f}s > {thr}s)")
        if ctx.perp_fetched_at is not None and is_stale(
            ctx.perp_fetched_at, threshold_s=thr, now=ctx.now
        ):
            age = age_seconds(ctx.perp_fetched_at, now=ctx.now)
            vetoes.append(f"perp data stale ({age:.1f}s > {thr}s)")
    else:
        if ctx.perp_fetched_at is not None:
            age = age_seconds(ctx.perp_fetched_at, now=ctx.now) or 0.0
            if age > ctx.mock_loose_stale_s:
                vetoes.append(f"perp data stale ({age:.0f}s)")

    if ctx.news_blackout:
        vetoes.append("news blackout window (IMMINENT / calendar)")

    # Stub hook still available; prefer real intel blackout via news_blackout
    if ctx.scheduled_event_within_30m:
        vetoes.append("scheduled macro/maintenance within 30m (stub)")
    if ctx.venue_not_operational:
        vetoes.append("venue status not operational (stub)")
    if ctx.risk_daily_loss_hit:
        vetoes.append("daily loss limit reached (stub)")
    if ctx.risk_margin_cap_hit:
        vetoes.append("position/margin cap reached (stub)")

    return VetoResult(vetoes=vetoes)


def stub_risk_state() -> dict[str, bool]:
    """Expose stub risk/news veto hooks for Operator / API transparency."""
    eng = (load_config().get("engine") or {}).get("vetoes") or {}
    return {
        "risk_daily_loss_stub": bool(eng.get("risk_daily_loss_stub", False)),
        "risk_margin_cap_stub": bool(eng.get("risk_margin_cap_stub", False)),
        "venue_not_operational_stub": bool(eng.get("venue_not_operational_stub", False)),
        "scheduled_event_within_30m_stub": bool(
            eng.get("scheduled_event_within_30m_stub", False)
        ),
        "note": "Stubs only — force NO-GO when raised; not live risk telemetry.",
    }


def apply_stub_hooks(ctx: VetoContext) -> VetoContext:
    stubs = stub_risk_state()
    ctx.risk_daily_loss_hit = ctx.risk_daily_loss_hit or stubs["risk_daily_loss_stub"]
    ctx.risk_margin_cap_hit = ctx.risk_margin_cap_hit or stubs["risk_margin_cap_stub"]
    ctx.venue_not_operational = (
        ctx.venue_not_operational or stubs["venue_not_operational_stub"]
    )
    ctx.scheduled_event_within_30m = (
        ctx.scheduled_event_within_30m or stubs["scheduled_event_within_30m_stub"]
    )
    return ctx
