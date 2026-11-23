"""Orderflow feature builder — CVD windows, imbalance, OI Δ, funding/basis.

Same code path for live snapshots and replay. Persists via StoreWriter
``orderflow_features`` (payload holds extras).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from crypto.providers.types import PerpSnapshot, TradePrint, utcnow


@dataclass
class OrderflowState:
    """Rolling perp tape + last OI for delta hooks."""

    trades: list[TradePrint] = field(default_factory=list)
    max_trades: int = 5000
    last_oi: Optional[float] = None
    last_oi_at: Optional[datetime] = None
    last_mark: Optional[float] = None
    last_index: Optional[float] = None

    def append_trades(self, trades: Iterable[TradePrint]) -> None:
        for t in trades:
            self.trades.append(t)
        if len(self.trades) > self.max_trades:
            self.trades = self.trades[-self.max_trades :]

    def note_snapshot(self, mark: float, index: float, oi: float, at: datetime) -> float:
        """Return OI delta vs previous note; update state."""
        delta = 0.0 if self.last_oi is None else float(oi) - float(self.last_oi)
        self.last_oi = float(oi)
        self.last_oi_at = at
        self.last_mark = float(mark)
        self.last_index = float(index)
        return delta


def cvd_over_window(
    trades: Sequence[TradePrint],
    *,
    window_s: float,
    now: Optional[datetime] = None,
) -> float:
    """Cumulative volume delta: buy size − sell size in the window."""
    now = now or utcnow()
    cutoff = now - timedelta(seconds=window_s)
    cvd = 0.0
    for t in trades:
        ts = t.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        if t.side == "buy":
            cvd += float(t.size)
        elif t.side == "sell":
            cvd -= float(t.size)
    return cvd


def _is_perp_symbol(symbol: str, underlying: str | None = None) -> bool:
    s = (symbol or "").upper()
    if "PERP" in s:
        return True
    u = (underlying or "").upper()
    if u and (f"{u}USD" in s or f"{u}USDT" in s):
        return True
    # Legacy BTC default match when underlying omitted
    if not u and ("BTCUSD" in s or "BTCUSDT" in s):
        return True
    return False


def compute_orderflow_features(
    perp: PerpSnapshot,
    *,
    state: Optional[OrderflowState] = None,
    option_signed_premium: Optional[dict[str, float]] = None,
    now: Optional[datetime] = None,
    underlying: str | None = None,
) -> dict[str, Any]:
    """Build orderflow feature dict for persistence + signal engine.

    Prefer CVD from the rolling tape when ``state`` has recent perp trades;
    otherwise keep the provider's precomputed CVD windows.
    """
    now = now or utcnow()
    fetched = perp.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)

    oi_delta = 0.0
    basis = float(perp.mark) - float(perp.index)
    basis_bps = (basis / perp.index * 1e4) if perp.index else 0.0

    cvd_1m = float(perp.cvd_1m)
    cvd_5m = float(perp.cvd_5m)
    cvd_15m = float(perp.cvd_15m)
    tape_source = "provider"

    if state is not None:
        oi_delta = state.note_snapshot(perp.mark, perp.index, perp.open_interest, fetched)
        # Prefer tape-derived CVD when we have perp prints in window
        perp_trades = [
            t for t in state.trades if _is_perp_symbol(t.symbol, underlying)
        ]
        if perp_trades:
            cvd_1m = cvd_over_window(perp_trades, window_s=60, now=now)
            cvd_5m = cvd_over_window(perp_trades, window_s=300, now=now)
            cvd_15m = cvd_over_window(perp_trades, window_s=900, now=now)
            tape_source = "tape"

    opt_flow = option_signed_premium or {}
    features = {
        "symbol": perp.symbol,
        "venue": perp.venue,
        "mark": float(perp.mark),
        "index": float(perp.index),
        "cvd_1m": cvd_1m,
        "cvd_5m": cvd_5m,
        "cvd_15m": cvd_15m,
        "book_imbalance": float(perp.book_imbalance),
        "funding_rate": float(perp.funding_rate),
        "open_interest": float(perp.open_interest),
        "oi_delta": oi_delta,
        "basis": basis,
        "basis_bps": basis_bps,
        "options_signed_call_premium": float(opt_flow.get("call") or 0.0),
        "options_signed_put_premium": float(opt_flow.get("put") or 0.0),
        "tape_source": tape_source,
        "source": perp.source,
        "computed_at": fetched,
        "hooks": {
            "funding": float(perp.funding_rate),
            "basis_bps": basis_bps,
            "oi_delta": oi_delta,
            "book_imbalance": float(perp.book_imbalance),
        },
    }
    return features


def orderflow_from_perp(perp: PerpSnapshot) -> dict[str, Any]:
    """Convenience when no rolling state is available."""
    return compute_orderflow_features(perp)
