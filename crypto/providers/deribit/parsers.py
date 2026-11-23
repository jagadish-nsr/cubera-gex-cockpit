"""Pure Deribit message parsers (fixture-testable, no network).

Field findings (Phase 0):
- Aggressor / taker side on trades: ``direction`` ∈ {"buy","sell"}
  (Deribit docs: "Trade direction of the taker").
- Gamma: ``greeks.gamma`` — standard Black-Scholes γ = ∂Δ/∂S per $1
  underlying move (no Deribit-specific rescale). mark_iv is percent
  (e.g. 67.67 → 67.67%).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from crypto.providers.types import OptionContract, TradePrint

# Documented once for status / API consumers.
FIELD_FINDINGS = {
    "aggressor_side_field": "direction",
    "aggressor_side_meaning": "taker side: buy | sell",
    "gamma_field": "greeks.gamma",
    "gamma_units": (
        "Black-Scholes gamma (∂delta/∂S) per $1 move of the underlying; "
        "standard BS without Deribit adjustments. Scale into GEX as "
        "Γ × S² × 0.01 × contract_size (contract_size=1 coin)."
    ),
    "mark_iv_units": "percent (67.67 means 67.67%)",
    "docs": {
        "trades": "https://docs.deribit.com/subscriptions/trades/tradeskindcurrencyinterval",
        "ticker": "https://docs.deribit.com/api-reference/market-data/public-ticker",
    },
}


def ms_to_dt(ms: int | float | None) -> datetime:
    if ms is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)


def parse_instrument_name(name: str) -> tuple[str, float, str]:
    """BTC-28NOV25-97000-C → (underlying, strike, C|P)."""
    parts = name.split("-")
    if len(parts) < 4:
        raise ValueError(f"unexpected deribit instrument: {name}")
    underlying = parts[0]
    strike = float(parts[-2])
    opt = parts[-1].upper()
    if opt not in {"C", "P"}:
        # call/put spelled out rarely; map
        if opt.startswith("C"):
            opt = "C"
        elif opt.startswith("P"):
            opt = "P"
        else:
            raise ValueError(f"bad option side in {name}")
    return underlying, strike, opt


def parse_option_ticker(
    payload: dict[str, Any],
    *,
    expiry: Optional[datetime] = None,
) -> OptionContract:
    """Map a Deribit option ticker (REST result or WS data) to OptionContract."""
    name = payload["instrument_name"]
    underlying, strike, opt = parse_instrument_name(name)
    greeks = payload.get("greeks") or {}
    stats = payload.get("stats") or {}
    mark_iv = payload.get("mark_iv")
    iv = float(mark_iv) / 100.0 if mark_iv is not None else 0.0
    bid = float(payload.get("best_bid_price") or 0)
    ask = float(payload.get("best_ask_price") or 0)
    mid = (bid + ask) / 2.0 if bid and ask else float(payload.get("mark_price") or 0)
    if expiry is None:
        # WS ticker may omit expiry; leave epoch placeholder — callers should overlay
        expiry = ms_to_dt(payload.get("expiration_timestamp"))
    return OptionContract(
        instrument=name,
        expiry=expiry,
        strike=strike,
        option_type=opt,
        oi=float(payload.get("open_interest") or 0),
        iv=iv,
        gamma=float(greeks.get("gamma") or 0),
        delta=float(greeks.get("delta") or 0),
        mid=mid,
        bid=bid,
        ask=ask,
        volume=float(stats.get("volume") or payload.get("stats_volume") or 0),
        underlying=underlying,
    )


def parse_trade(payload: dict[str, Any], *, venue: str = "deribit") -> TradePrint:
    """Map a Deribit trade object to TradePrint.

    Aggressor = ``direction`` (taker buy/sell).
    """
    direction = (payload.get("direction") or "unknown").lower()
    if direction not in {"buy", "sell"}:
        direction = "unknown"
    return TradePrint(
        venue=venue,
        symbol=str(payload.get("instrument_name") or ""),
        price=float(payload.get("price") or 0),
        size=float(payload.get("amount") or payload.get("contracts") or 0),
        side=direction,
        ts=ms_to_dt(payload.get("timestamp")),
        is_block=bool(payload.get("block_trade_id") or payload.get("block_trade_leg_count")),
    )


def parse_ws_subscription(message: dict[str, Any]) -> tuple[str, Any] | None:
    """Return (channel, data) for a Deribit subscription notification, else None."""
    if message.get("method") != "subscription":
        return None
    params = message.get("params") or {}
    channel = params.get("channel")
    data = params.get("data")
    if not channel:
        return None
    return str(channel), data


def unwrap_trades(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        return [data]
    return []
