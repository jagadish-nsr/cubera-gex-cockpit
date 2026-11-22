"""Minimal replay over recorded ticks using the same feature path as live.

Reads raw option-chain / trade / perp payloads (from fixtures or SQLite
``raw_ticks``) and runs GEX → orderflow → signal → optional inference.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from crypto.engine.flow_inventory import FlowInventory
from crypto.engine.gex import compute_gex_features
from crypto.engine.inference import run_inference
from crypto.engine.orderflow import OrderflowState, compute_orderflow_features
from crypto.engine.signals import build_signal_card
from crypto.providers.deribit.parsers import parse_trade
from crypto.providers.types import (
    OptionChainSnapshot,
    OptionContract,
    PerpSnapshot,
    TradePrint,
    utcnow,
)


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # ms or s heuristic
        v = float(value)
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return utcnow()


def chain_from_payload(payload: dict[str, Any], *, source: str = "replay") -> OptionChainSnapshot:
    """Rebuild OptionChainSnapshot from a stored chain payload."""
    contracts: list[OptionContract] = []
    for c in payload.get("contracts") or []:
        contracts.append(
            OptionContract(
                instrument=str(c["instrument"]),
                expiry=_parse_ts(c.get("expiry")),
                strike=float(c["strike"]),
                option_type=str(c["option_type"]),
                oi=float(c.get("oi") or 0),
                iv=float(c.get("iv") or 0),
                gamma=float(c.get("gamma") or 0),
                delta=float(c.get("delta") or 0),
                mid=float(c.get("mid") or 0),
                bid=float(c.get("bid") or 0),
                ask=float(c.get("ask") or 0),
                volume=float(c.get("volume") or 0),
                underlying=str(payload.get("underlying") or "BTC"),
            )
        )
    fetched = _parse_ts(payload.get("fetched_at") or payload.get("timestamp"))
    return OptionChainSnapshot(
        underlying=str(payload.get("underlying") or "BTC"),
        spot=float(payload["spot"]),
        contracts=contracts,
        source=source,
        fetched_at=fetched,
        mark_iv=payload.get("mark_iv"),
        dvol=payload.get("dvol"),
    )


def trades_from_payloads(rows: Iterable[dict[str, Any]]) -> list[TradePrint]:
    out: list[TradePrint] = []
    for row in rows:
        # Accept already-normalized or raw Deribit trade objects
        if "direction" in row or "instrument_name" in row:
            out.append(parse_trade(row))
        else:
            out.append(
                TradePrint(
                    venue=str(row.get("venue") or "replay"),
                    symbol=str(row.get("symbol") or row.get("instrument") or ""),
                    price=float(row.get("price") or 0),
                    size=float(row.get("size") or row.get("amount") or 0),
                    side=str(row.get("side") or "unknown"),
                    ts=_parse_ts(row.get("ts") or row.get("timestamp")),
                    is_block=bool(row.get("is_block")),
                )
            )
    return out


def perp_from_payload(payload: dict[str, Any], *, source: str = "replay") -> PerpSnapshot:
    return PerpSnapshot(
        venue=str(payload.get("venue") or "replay"),
        symbol=str(payload.get("symbol") or "BTC-PERPETUAL"),
        mark=float(payload.get("mark") or payload.get("spot") or 0),
        index=float(payload.get("index") or payload.get("mark") or 0),
        funding_rate=float(payload.get("funding_rate") or 0),
        open_interest=float(payload.get("open_interest") or 0),
        cvd_1m=float(payload.get("cvd_1m") or 0),
        cvd_5m=float(payload.get("cvd_5m") or 0),
        cvd_15m=float(payload.get("cvd_15m") or 0),
        book_imbalance=float(payload.get("book_imbalance") or 0),
        fetched_at=_parse_ts(payload.get("fetched_at") or payload.get("timestamp")),
        source=source,
    )


def replay_features(
    *,
    chain: OptionChainSnapshot,
    trades: Optional[list[TradePrint]] = None,
    perp: Optional[PerpSnapshot] = None,
    news_blackout: bool = False,
) -> dict[str, Any]:
    """Same feature path as CockpitService.snapshot (minus network)."""
    inv = FlowInventory(source="replay_signed_tape")
    if trades:
        inv.ingest_many(trades)

    gex = compute_gex_features(chain, flow_inventory=inv if inv.total_trades else None)

    if perp is None:
        perp = PerpSnapshot(
            venue="replay",
            symbol=f"{chain.underlying}-PERPETUAL",
            mark=chain.spot,
            index=chain.spot,
            funding_rate=0.0,
            open_interest=0.0,
            cvd_1m=0.0,
            cvd_5m=0.0,
            cvd_15m=0.0,
            book_imbalance=0.0,
            fetched_at=chain.fetched_at,
            source="replay",
        )

    of_state = OrderflowState()
    if trades:
        of_state.append_trades(
            [t for t in trades if "PERP" in t.symbol.upper() or t.symbol.endswith("USD")]
        )
    orderflow = compute_orderflow_features(perp, state=of_state)

    as_of = perp.fetched_at or chain.fetched_at
    card = build_signal_card(
        gex,
        perp,
        orderflow=orderflow,
        news_blackout=news_blackout,
        chain_fetched_at=chain.fetched_at,
        live_signal_path=False,
        now=as_of,
    )
    inference = run_inference(gex, orderflow)

    return {
        "gex": gex,
        "orderflow": orderflow,
        "signal": card,
        "inference": inference,
        "flow_inventory": inv.summary(),
    }


def load_recorded_bundle(path: Path | str) -> dict[str, Any]:
    """Load a JSON bundle with chain / trades / perp keys (test fixtures)."""
    p = Path(path)
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def replay_recorded_file(path: Path | str) -> dict[str, Any]:
    data = load_recorded_bundle(path)
    # Support Phase 0 recorded_samples shape + full fixture shape
    if "contracts" in data:
        chain = chain_from_payload(data, source="replay-file")
    elif "chain" in data:
        chain = chain_from_payload(data["chain"], source="replay-file")
    elif "deribit_option_ticker" in data:
        # Minimal: build a tiny chain from one ticker + sibling puts/calls if present
        raise ValueError(
            "recorded_samples ticker-only file needs a full chain bundle; "
            "use crypto/fixtures/btc_chain.json or a chain snapshot export"
        )
    else:
        raise ValueError(f"unrecognized replay bundle keys: {list(data)[:8]}")

    trade_rows = data.get("trades") or data.get("deribit_trades") or []
    trades = trades_from_payloads(trade_rows) if trade_rows else None

    perp_payload = data.get("perp")
    perp = perp_from_payload(perp_payload, source="replay-file") if perp_payload else None

    result = replay_features(chain=chain, trades=trades, perp=perp)
    result["bundle"] = str(path)
    return result
