"""commentary/models.py — Data contracts and domain models for Session Commentary Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Sequence

IST = timezone(timedelta(hours=5, minutes=30))


class PointInTimeViolation(Exception):
    """Raised when an operation attempts to access data beyond the declared timestamp."""
    pass


@dataclass
class PointInTimeView:
    """Read-only view of market data strictly bounded at as_of timestamp.
    
    Guarantees no future data is leaked into detectors or scoring.
    """
    as_of: datetime
    symbol: str
    spot: float
    prior_close: float
    atm_iv: float
    expected_move_1d: float
    call_wall: float
    put_wall: float
    gamma_flip: float
    max_pain: float
    net_gex_sign: int                       # +1 for positive gamma, -1 for negative gamma
    candles_1m: list[dict[str, Any]] = field(default_factory=list)
    sentinel_signals: list[dict[str, Any]] = field(default_factory=list)
    news_items: list[dict[str, Any]] = field(default_factory=list)
    pinned_recommendations: list[dict[str, Any]] = field(default_factory=list)
    extra_facts: dict[str, Any] = field(default_factory=dict)

    def validate_timestamp(self, ts: datetime) -> None:
        """Enforce strict point-in-time guard."""
        if ts > self.as_of:
            raise PointInTimeViolation(
                f"Look-ahead violation: accessed data timestamp {ts} is after as_of {self.as_of}"
            )

    @property
    def sigma_1d(self) -> float:
        """Return 1-day expected move in points (sigma)."""
        if self.expected_move_1d > 0:
            return self.expected_move_1d
        # Default ~ 1-day IV approximation: spot * iv * sqrt(1/365)
        return max(5.0, self.spot * max(0.08, self.atm_iv) * (1.0 / (365.0 ** 0.5)))


@dataclass(frozen=True)
class Event:
    """Structured, verified market event emitted by a detector."""
    id: str
    ts: datetime
    type: str                               # e.g. GAP_UP, WALL_TEST, IMPULSE_MOVE, PINNED_TARGET_APPROACH
    facts: dict[str, Any]                   # Strict numeric facts, e.g. {"spot": 24800.0, "wall": 24800.0, "sigma": 0.08}
    magnitude_sigma: float                  # Regime-normalized displacement in sigma units
    quality_flags: list[str] = field(default_factory=list)
    related_ids: list[str] = field(default_factory=list)


@dataclass
class ScoredEvent:
    """Event enriched with significance scoring, confluence merging, and reasoning."""
    event: Event
    significance: float                     # 0.0 to 1.0
    reasons: list[str] = field(default_factory=list)
    merged_event_ids: list[str] = field(default_factory=list)


@dataclass
class SuggestionBite:
    """Conditional, level-anchored guidance attached to a commentary entry."""
    id: str
    entry_id: str
    ts: datetime
    type: str                               # WATCH, PREPARE, ACT-IF, MANAGE, STAND-ASIDE, HEDGE
    headline: str
    condition: dict[str, Any]               # {"field": "close_5m", "op": ">", "val": 24800.0}
    action_frame: str                       # e.g. "Bull Call Spread 24800/25000"
    structure: dict[str, Any]               # legs, strike, expiry, lot_size
    levels: dict[str, float]                # {"trigger": 24800.0, "invalidation": 24730.0, "target": 24980.0}
    risk: dict[str, Any]                    # {"max_loss_per_lot": 3500.0, "rr": 1.8, "breakeven": 24855.0}
    valid_until: datetime
    scenario_link: str                      # "bull" | "bear" | "base"
    badge: dict[str, Any]                   # {"calibrated": False, "mode": "shadow", "n_samples": 0}
    facts_used: list[str]
    state: str = "ISSUED"                   # ISSUED, TRIGGERED, TARGET_HIT, STOPPED, INVALIDATED, EXPIRED
    resolved_ts: datetime | None = None
    outcome: dict[str, Any] | None = None
    pinned_rec_id: str | None = None        # Set if linked to an active pinned recommendation


@dataclass
class StoryState:
    """Running narrative state across the trading session."""
    phase: str                              # pre_open, opening_15m, morning, midday, afternoon, closing_30m
    running_thesis: dict[str, Any] = field(default_factory=lambda: {
        "bias": "unclear",
        "confidence": 0.5,
        "since_ts": None,
    })
    open_threads: list[dict[str, Any]] = field(default_factory=list)
    entries: list[CommentaryEntry] = field(default_factory=list)

    def close_thread(self, thread_id: str) -> None:
        self.open_threads = [t for t in self.open_threads if t.get("thread_id") != thread_id]


@dataclass
class CommentaryEntry:
    """Narrative entry displayed in the commentary stream."""
    id: str
    ts: datetime
    phase: str
    event_ids: list[str]
    headline: str
    body: str
    bites: list[SuggestionBite] = field(default_factory=list)
    chart_ref: str | None = None
    verified: bool = True
    template_fallback: bool = True
    tags: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
