"""Dealer position estimate q_i from Deribit signed option trades.

Design (crypto-gex-design-plan):
  Flow model q_i = −cumulative customer-signed volume since the series listed.

Customer-signed volume uses the taker aggressor field ``direction``
(buy | sell). Block trades are stripped (tagged separately, not in q_i).

  buy  → customer +size
  sell → customer −size
  q_i  = −customer_cum   (dealer inventory)

When no tape is available, callers fall back to the volume-skew proxy in
``compute_gex_features``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from crypto.providers.deribit.parsers import parse_instrument_name
from crypto.providers.types import TradePrint


@dataclass
class InstrumentFlow:
    """Per-instrument cumulative customer-signed volume and derived q_i."""

    instrument: str
    strike: float
    option_type: str  # C | P
    customer_signed: float = 0.0
    trade_count: int = 0
    block_skipped: int = 0
    last_ts: Optional[datetime] = None

    @property
    def q_i(self) -> float:
        return -self.customer_signed


@dataclass
class FlowInventory:
    """In-memory accumulator of signed option tape → q_i per instrument."""

    by_instrument: dict[str, InstrumentFlow] = field(default_factory=dict)
    total_trades: int = 0
    total_blocks_skipped: int = 0
    source: str = "signed_tape"

    def ingest(self, trade: TradePrint, *, strip_blocks: bool = True) -> bool:
        """Apply one trade. Returns True if it updated inventory."""
        if not trade.symbol or "-" not in trade.symbol:
            return False
        # Perp / futures prints do not belong in option q_i
        if "PERPETUAL" in trade.symbol.upper() or trade.symbol.count("-") < 3:
            return False
        if strip_blocks and trade.is_block:
            self.total_blocks_skipped += 1
            key = trade.symbol
            row = self.by_instrument.get(key)
            if row is None:
                try:
                    _, strike, opt = parse_instrument_name(trade.symbol)
                except ValueError:
                    return False
                row = InstrumentFlow(
                    instrument=trade.symbol, strike=strike, option_type=opt
                )
                self.by_instrument[key] = row
            row.block_skipped += 1
            return False
        if trade.side not in {"buy", "sell"}:
            return False
        try:
            _, strike, opt = parse_instrument_name(trade.symbol)
        except ValueError:
            return False

        row = self.by_instrument.get(trade.symbol)
        if row is None:
            row = InstrumentFlow(
                instrument=trade.symbol, strike=strike, option_type=opt
            )
            self.by_instrument[trade.symbol] = row

        signed = float(trade.size) if trade.side == "buy" else -float(trade.size)
        row.customer_signed += signed
        row.trade_count += 1
        row.last_ts = trade.ts
        self.total_trades += 1
        return True

    def ingest_many(self, trades: Iterable[TradePrint], *, strip_blocks: bool = True) -> int:
        n = 0
        for t in trades:
            if self.ingest(t, strip_blocks=strip_blocks):
                n += 1
        return n

    def q_by_instrument(self) -> dict[str, float]:
        return {k: v.q_i for k, v in self.by_instrument.items()}

    def q_by_strike_side(self) -> dict[tuple[float, str], float]:
        """Aggregate q_i across expiries at the same (strike, C|P)."""
        out: dict[tuple[float, str], float] = defaultdict(float)
        for row in self.by_instrument.values():
            out[(row.strike, row.option_type)] += row.q_i
        return dict(out)

    def near_spot_flow_sign(
        self, spot: float, *, band_pct: float = 0.03
    ) -> tuple[str, float]:
        """Sign of dealer gamma-proxy from q_i near spot (no Γ yet).

        Positive q on calls and negative q on puts → positive-gamma lean
        (same reading as convention: +OI calls / −OI puts).
        """
        total = 0.0
        for row in self.by_instrument.values():
            if abs(row.strike - spot) / spot > band_pct:
                continue
            # Same sign mapping as convention: call q contributes +, put q as-is
            # (put q already negative when dealers short puts / customers long puts)
            if row.option_type == "C":
                total += row.q_i
            else:
                total += row.q_i  # put q_i already signed
        if total > 1e-9:
            return "positive", total
        if total < -1e-9:
            return "negative", total
        return "flat", total

    def summary(self) -> dict[str, Any]:
        return {
            "instruments": len(self.by_instrument),
            "total_trades": self.total_trades,
            "blocks_skipped": self.total_blocks_skipped,
            "source": self.source,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "total_trades": self.total_trades,
            "total_blocks_skipped": self.total_blocks_skipped,
            "instruments": {
                k: {
                    "strike": v.strike,
                    "option_type": v.option_type,
                    "customer_signed": v.customer_signed,
                    "q_i": v.q_i,
                    "trade_count": v.trade_count,
                    "block_skipped": v.block_skipped,
                    "last_ts": v.last_ts.isoformat() if v.last_ts else None,
                }
                for k, v in self.by_instrument.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowInventory":
        inv = cls(
            source=str(data.get("source") or "signed_tape"),
            total_trades=int(data.get("total_trades") or 0),
            total_blocks_skipped=int(data.get("total_blocks_skipped") or 0),
        )
        for name, row in (data.get("instruments") or {}).items():
            inv.by_instrument[name] = InstrumentFlow(
                instrument=name,
                strike=float(row["strike"]),
                option_type=str(row["option_type"]),
                customer_signed=float(row.get("customer_signed") or 0),
                trade_count=int(row.get("trade_count") or 0),
                block_skipped=int(row.get("block_skipped") or 0),
            )
        return inv


def customer_signed_from_direction(direction: str, size: float) -> float:
    """Pure helper for tests: buy → +size, sell → −size."""
    d = (direction or "").lower()
    if d == "buy":
        return float(size)
    if d == "sell":
        return -float(size)
    return 0.0
