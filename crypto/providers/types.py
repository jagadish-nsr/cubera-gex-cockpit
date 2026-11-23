"""Stable provider interfaces for CryptoCubera.

Narrow contracts so Deribit / Delta / Binance / mock swap by config without
touching the GEX engine or UI. Execution is paper-only in the foundation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class OptionContract:
    instrument: str
    expiry: datetime
    strike: float
    option_type: str  # "C" | "P"
    oi: float
    iv: float
    gamma: float
    delta: float
    mid: float
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    underlying: str = "BTC"


@dataclass
class OptionChainSnapshot:
    underlying: str
    spot: float
    contracts: list[OptionContract]
    source: str
    fetched_at: datetime
    exchange_ts: Optional[datetime] = None
    mark_iv: Optional[float] = None
    dvol: Optional[float] = None

    def validate(self) -> None:
        if self.spot <= 0:
            raise ValueError(f"invalid spot: {self.spot}")
        if not self.contracts:
            raise ValueError("empty option chain")
        for c in self.contracts:
            if c.option_type not in {"C", "P"}:
                raise ValueError(f"bad option_type {c.option_type!r}")


@dataclass
class TradePrint:
    venue: str
    symbol: str
    price: float
    size: float
    side: str  # "buy" | "sell" | "unknown"
    ts: datetime
    is_block: bool = False


@dataclass
class PerpSnapshot:
    venue: str
    symbol: str
    mark: float
    index: float
    funding_rate: float
    open_interest: float
    cvd_1m: float
    cvd_5m: float
    cvd_15m: float
    book_imbalance: float  # -1..+1 bid-heavy negative
    fetched_at: datetime
    source: str


@dataclass
class NewsEvent:
    """Provider-facing news row; intel pipeline normalizes to IntelEvent."""

    event_id: str
    title: str
    source: str
    tier: str  # IMMINENT | BREAKING | WATCH
    score: float
    assets: list[str] = field(default_factory=list)
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    impact_note: str = ""
    # Extended schema (optional on providers; filled by intel pipeline)
    event_type: str = "other"
    venues: list[str] = field(default_factory=list)
    occurs_at: Optional[datetime] = None
    blackout_start: Optional[datetime] = None
    blackout_until: Optional[datetime] = None
    novelty: float = 1.0
    source_reliability: float = 0.0
    prior_move_proxy: float = 0.0
    market_confirmation: float = 0.0
    engine_action: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderMeta:
    name: str
    kind: str  # option_chain | perp | news | execution
    enabled: bool
    priority: int
    feature_flag: str
    credentials_ref: Optional[str] = None
    rate_limits: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class OptionChainProvider(Protocol):
    name: str

    async def fetch_chain(self, underlying: str = "BTC") -> OptionChainSnapshot: ...


@runtime_checkable
class PerpProvider(Protocol):
    name: str

    async def fetch_perp(self, symbol: str = "BTC-PERP") -> PerpSnapshot: ...


@runtime_checkable
class NewsProvider(Protocol):
    name: str

    async def fetch_events(self, limit: int = 20) -> list[NewsEvent]: ...


@runtime_checkable
class ExecutionVenue(Protocol):
    name: str

    async def status(self) -> dict[str, Any]: ...

    async def place_bracket(self, order: dict[str, Any]) -> dict[str, Any]: ...

    async def cancel_all(self) -> dict[str, Any]: ...
