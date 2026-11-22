"""Pure Delta Exchange India message parsers (fixture-testable).

Field findings (Phase 0):
- Public WS ``trades`` channel does **not** send an explicit buy/sell.
  Aggressor is recovered from ``r`` (buyer_role):
    - ``r == "t"`` → buyer was taker → aggressor side = buy
    - ``r == "m"`` → buyer was maker → aggressor side = sell
- REST public trades use ``side`` = aggressor buy|sell directly.
- Compact ticker WS uses ``g[1]`` for gamma (∂Δ/∂S of underlying).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from crypto.providers.types import OptionContract, TradePrint

FIELD_FINDINGS = {
    "ws_trades_aggressor_field": "r",
    "ws_trades_aggressor_meaning": (
        "buyer_role: 't'=buyer was taker → aggressor buy; "
        "'m'=buyer was maker → aggressor sell"
    ),
    "rest_trades_aggressor_field": "side",
    "rest_trades_aggressor_meaning": "aggressor buy | sell",
    "ws_ticker_gamma_path": "d[].g[1] (compact) or greeks.gamma (REST)",
    "gamma_units": "∂delta/∂S per $1 underlying (same BS sense as Deribit)",
    "ws_url": "wss://public-socket.india.delta.exchange",
    "docs": "https://docs.delta.exchange/#trades",
}


def us_to_dt(us: int | float | None) -> datetime:
    """Delta WS timestamps are microseconds since epoch."""
    if us is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(float(us) / 1_000_000.0, tz=timezone.utc)


def aggressor_from_buyer_role(buyer_role: str | None) -> str:
    role = (buyer_role or "").lower()
    if role in {"t", "taker"}:
        return "buy"
    if role in {"m", "maker"}:
        return "sell"
    return "unknown"


def parse_ws_trade(payload: dict[str, Any], *, venue: str = "delta") -> TradePrint:
    """Parse compact Delta public WS trades message."""
    side = aggressor_from_buyer_role(payload.get("r") or payload.get("buyer_role"))
    # Prefer trade time ``t``; fall back to publish ``ts``.
    ts_raw = payload.get("t") if payload.get("t") is not None else payload.get("ts")
    return TradePrint(
        venue=venue,
        symbol=str(payload.get("sy") or payload.get("symbol") or ""),
        price=float(payload.get("p") or payload.get("price") or 0),
        size=float(payload.get("s") or payload.get("size") or 0),
        side=side,
        ts=us_to_dt(ts_raw),
        is_block=False,
    )


def parse_rest_trade(payload: dict[str, Any], *, venue: str = "delta") -> TradePrint:
    side = (payload.get("side") or "unknown").lower()
    if side not in {"buy", "sell"}:
        side = "unknown"
    ts_raw = payload.get("timestamp")
    if isinstance(ts_raw, (int, float)) and ts_raw > 1e14:
        ts = us_to_dt(ts_raw)
    elif isinstance(ts_raw, (int, float)) and ts_raw > 1e11:
        ts = datetime.fromtimestamp(float(ts_raw) / 1000.0, tz=timezone.utc)
    elif isinstance(ts_raw, (int, float)):
        ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    return TradePrint(
        venue=venue,
        symbol=str(payload.get("symbol") or ""),
        price=float(payload.get("price") or 0),
        size=float(payload.get("size") or 0),
        side=side,
        ts=ts,
        is_block=False,
    )


def parse_option_symbol(symbol: str) -> tuple[str, float, str, Optional[str]]:
    """C-BTC-61400-241126 → (BTC, 61400, C, 241126)."""
    parts = symbol.split("-")
    if len(parts) < 4:
        raise ValueError(f"unexpected delta option symbol: {symbol}")
    opt = parts[0].upper()
    if opt not in {"C", "P"}:
        raise ValueError(f"bad option type in {symbol}")
    underlying = parts[1]
    strike = float(parts[2])
    expiry_code = parts[3]
    return underlying, strike, opt, expiry_code


def _parse_ddmmyy(code: str) -> datetime:
    # 241126 → 2026-11-24 08:00 UTC stand-in (Delta expiry clock VERIFY-live)
    if len(code) != 6:
        return datetime.now(timezone.utc)
    dd, mm, yy = int(code[0:2]), int(code[2:4]), int(code[4:6])
    return datetime(2000 + yy, mm, dd, 8, 0, tzinfo=timezone.utc)


def parse_rest_option_ticker(payload: dict[str, Any]) -> OptionContract:
    symbol = payload["symbol"]
    underlying, strike, opt, expiry_code = parse_option_symbol(symbol)
    greeks = payload.get("greeks") or {}
    quotes = payload.get("quotes") or {}
    gamma = float(greeks.get("gamma") or 0)
    delta = float(greeks.get("delta") or 0)
    mark_iv = quotes.get("mark_iv")
    try:
        iv = abs(float(mark_iv)) if mark_iv is not None else 0.0
    except (TypeError, ValueError):
        iv = 0.0
    bid = float(quotes.get("best_bid") or 0)
    ask = float(quotes.get("best_ask") or 0)
    mid = (bid + ask) / 2.0 if bid and ask else float(payload.get("mark_price") or payload.get("close") or 0)
    return OptionContract(
        instrument=symbol,
        expiry=_parse_ddmmyy(expiry_code or "010170"),
        strike=strike,
        option_type=opt,
        oi=float(payload.get("oi") or 0),
        iv=iv,
        gamma=gamma,
        delta=delta,
        mid=mid,
        bid=bid,
        ask=ask,
        volume=float(payload.get("volume") or 0),
        underlying=underlying,
    )


def parse_compact_ticker_greeks(g: list[Any] | None) -> tuple[float, float]:
    """Return (delta, gamma) from compact WS ticker ``g`` array."""
    if not g or len(g) < 2:
        return 0.0, 0.0
    return float(g[0] or 0), float(g[1] or 0)
