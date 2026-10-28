"""Fields a GEX publish must carry so the line can be checked against the book."""

from __future__ import annotations


def _num(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _lot(symbol: str | None) -> int | None:
    if not symbol:
        return None
    try:
        import config
        index = config.INDICES.get(str(symbol).upper())
    except Exception:
        return None
    if index is None:
        return None
    try:
        return int(index.lot_size)
    except (TypeError, ValueError):
        return None


def _sign_word(net) -> str | None:
    if net is None:
        return None
    if net > 0:
        return "call-heavy"
    if net < 0:
        return "put-heavy"
    return "flat"


def book_fields(data) -> dict:
    """Lot, sign model, OI time, and the strikes on screen. No chain and no ticks."""
    symbol = getattr(data, "symbol", None)
    try:
        levels = getattr(data, "levels", None)
        if levels is None:
            return {"symbol": symbol}
        net = _num(getattr(levels, "total_net_gex", None))
        oi = getattr(levels, "oi_timestamp", None)
        if hasattr(oi, "isoformat"):
            oi = oi.isoformat()
        elif oi is not None:
            oi = str(oi)
        return {
            "symbol": symbol,
            "contract_size": _lot(symbol),
            "dealer_sign_model": getattr(levels, "dealer_sign_model", None),
            "oi_timestamp": oi,
            "oi_stale": bool(getattr(levels, "oi_stale", False)),
            "spot": _num(getattr(levels, "spot", None)),
            "call_wall": _num(getattr(levels, "call_wall", None)),
            "put_wall": _num(getattr(levels, "put_wall", None)),
            "flip": _num(getattr(levels, "flip", None)),
            "sign": _sign_word(net),
        }
    except Exception:
        return {"symbol": symbol}
