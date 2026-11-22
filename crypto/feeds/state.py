"""Shared in-memory state for live Deribit / Delta collectors."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from crypto.engine.flow_inventory import FlowInventory
from crypto.feeds.stale import STALE_THRESHOLD_S, age_seconds, is_stale
from crypto.providers.types import OptionChainSnapshot, TradePrint, utcnow


@dataclass
class VenueHealth:
    venue: str
    connected: bool = False
    last_msg_at: Optional[datetime] = None
    last_error: Optional[str] = None
    reconnects: int = 0
    ticks_persisted: int = 0
    channels: list[str] = field(default_factory=list)

    def mark_msg(self) -> None:
        self.last_msg_at = utcnow()
        self.connected = True
        self.last_error = None

    def mark_error(self, err: str) -> None:
        self.last_error = err
        self.connected = False

    def to_dict(self, *, threshold_s: float = STALE_THRESHOLD_S) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "connected": self.connected,
            "last_msg_at": self.last_msg_at.isoformat() if self.last_msg_at else None,
            "age_s": age_seconds(self.last_msg_at),
            "stale": is_stale(self.last_msg_at, threshold_s=threshold_s),
            "last_error": self.last_error,
            "reconnects": self.reconnects,
            "ticks_persisted": self.ticks_persisted,
            "channels": list(self.channels),
        }


class LiveFeedState:
    """Process-wide cache of the latest chain / tape / health."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.deribit_chain: Optional[OptionChainSnapshot] = None
        self.delta_chain: Optional[OptionChainSnapshot] = None
        self.recent_trades: list[TradePrint] = []
        self.max_trades = 500
        self.flow_inventory = FlowInventory()
        # Reject late writes from a dying BTC collector after ETH/SOL switch.
        self.active_underlying: Optional[str] = None
        self.health: dict[str, VenueHealth] = {
            "deribit": VenueHealth(venue="deribit"),
            "delta": VenueHealth(venue="delta"),
        }
        self.field_findings: dict[str, Any] = {}
        self.stale_threshold_s = STALE_THRESHOLD_S

    def set_active_underlying(self, underlying: str | None) -> None:
        with self._lock:
            self.active_underlying = (
                str(underlying).strip().upper() if underlying else None
            )

    def set_deribit_chain(self, snap: OptionChainSnapshot) -> None:
        with self._lock:
            if self.active_underlying and not self._matches_underlying(
                snap, self.active_underlying
            ):
                return
            self.deribit_chain = snap
            h = self.health["deribit"]
            h.mark_msg()

    def set_delta_chain(self, snap: OptionChainSnapshot) -> None:
        with self._lock:
            if self.active_underlying and not self._matches_underlying(
                snap, self.active_underlying
            ):
                return
            self.delta_chain = snap
            h = self.health["delta"]
            h.mark_msg()

    @staticmethod
    def _matches_underlying(snap: Optional[OptionChainSnapshot], underlying: str | None) -> bool:
        if snap is None:
            return False
        if underlying is None:
            return True
        want = str(underlying).strip().upper()
        got = str(snap.underlying or "").strip().upper()
        return bool(want) and got == want

    def get_deribit_chain(
        self, *, for_underlying: str | None = None
    ) -> Optional[OptionChainSnapshot]:
        """Return cached Deribit chain; optionally require matching underlying.

        Asset switches must not reuse a BTC cache for ETH/SOL (spot would stay
        BTC-like while the instrument label flips).
        """
        with self._lock:
            snap = self.deribit_chain
            if for_underlying is not None and not self._matches_underlying(snap, for_underlying):
                return None
            return snap

    def get_delta_chain(
        self, *, for_underlying: str | None = None
    ) -> Optional[OptionChainSnapshot]:
        with self._lock:
            snap = self.delta_chain
            if for_underlying is not None and not self._matches_underlying(snap, for_underlying):
                return None
            return snap

    def clear_for_underlying_switch(self, *, underlying: str | None = None) -> None:
        """Drop chain/tape caches so the next asset does not inherit the prior book."""
        with self._lock:
            if underlying is not None:
                self.active_underlying = str(underlying).strip().upper() or None
            self.deribit_chain = None
            self.delta_chain = None
            self.recent_trades = []
            self.flow_inventory = FlowInventory()

    def append_trade(self, trade: TradePrint) -> None:
        with self._lock:
            self.recent_trades.append(trade)
            if len(self.recent_trades) > self.max_trades:
                self.recent_trades = self.recent_trades[-self.max_trades :]
            self.flow_inventory.ingest(trade)

    def get_flow_inventory(self) -> FlowInventory:
        with self._lock:
            return self.flow_inventory

    def bump_ticks(self, venue: str, n: int = 1) -> None:
        with self._lock:
            h = self.health.get(venue)
            if h:
                h.ticks_persisted += n
                h.mark_msg()

    def note_reconnect(self, venue: str) -> None:
        with self._lock:
            h = self.health.get(venue)
            if h:
                h.reconnects += 1
                h.connected = False

    def note_error(self, venue: str, err: str) -> None:
        with self._lock:
            h = self.health.get(venue)
            if h:
                h.mark_error(err)

    def set_channels(self, venue: str, channels: list[str]) -> None:
        with self._lock:
            h = self.health.get(venue)
            if h:
                h.channels = list(channels)

    def set_field_findings(self, findings: dict[str, Any]) -> None:
        with self._lock:
            self.field_findings.update(findings)

    def signal_data_stale(self) -> tuple[bool, Optional[str]]:
        """True when live Deribit chain is on but older than threshold."""
        with self._lock:
            snap = self.deribit_chain
            if snap is None:
                return False, None
            ts = snap.fetched_at or snap.exchange_ts
            if is_stale(ts, threshold_s=self.stale_threshold_s):
                return True, f"deribit chain stale ({age_seconds(ts):.1f}s > {self.stale_threshold_s}s)"
            return False, None

    def status(self) -> dict[str, Any]:
        with self._lock:
            thr = self.stale_threshold_s
            return {
                "stale_threshold_s": thr,
                "active_underlying": self.active_underlying,
                "deribit_chain": {
                    "present": self.deribit_chain is not None,
                    "underlying": self.deribit_chain.underlying if self.deribit_chain else None,
                    "source": self.deribit_chain.source if self.deribit_chain else None,
                    "spot": self.deribit_chain.spot if self.deribit_chain else None,
                    "n_contracts": len(self.deribit_chain.contracts) if self.deribit_chain else 0,
                    "fetched_at": (
                        self.deribit_chain.fetched_at.isoformat() if self.deribit_chain else None
                    ),
                    "stale": is_stale(
                        self.deribit_chain.fetched_at if self.deribit_chain else None,
                        threshold_s=thr,
                    ),
                },
                "delta_chain": {
                    "present": self.delta_chain is not None,
                    "underlying": self.delta_chain.underlying if self.delta_chain else None,
                    "source": self.delta_chain.source if self.delta_chain else None,
                    "n_contracts": len(self.delta_chain.contracts) if self.delta_chain else 0,
                },
                "recent_trades": len(self.recent_trades),
                "flow_inventory": self.flow_inventory.summary(),
                "health": {k: v.to_dict(threshold_s=thr) for k, v in self.health.items()},
                "field_findings": dict(self.field_findings),
            }


_STATE: LiveFeedState | None = None


def get_feed_state(*, reload: bool = False) -> LiveFeedState:
    global _STATE
    if _STATE is None or reload:
        _STATE = LiveFeedState()
    return _STATE
