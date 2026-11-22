"""Build a static Deribit option-chain snapshot via public REST.

Used to seed the live cache (and as a one-shot GEX input) before / beside WS
ticker updates. Never raises to the UI — callers catch.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Optional

import httpx

from crypto.providers.deribit.parsers import ms_to_dt, parse_option_ticker
from crypto.providers.types import OptionChainSnapshot, OptionContract, utcnow

log = logging.getLogger(__name__)

DEFAULT_REST = "https://www.deribit.com/api/v2"


async def fetch_index_price(client: httpx.AsyncClient, base: str, currency: str = "BTC") -> float:
    url = f"{base.rstrip('/')}/public/ticker"
    resp = await client.get(url, params={"instrument_name": f"{currency}-PERPETUAL"})
    resp.raise_for_status()
    body = resp.json().get("result") or {}
    return float(body.get("index_price") or body.get("mark_price") or 0)


async def list_active_options(
    client: httpx.AsyncClient, base: str, currency: str = "BTC"
) -> list[dict[str, Any]]:
    url = f"{base.rstrip('/')}/public/get_instruments"
    resp = await client.get(
        url, params={"currency": currency, "kind": "option", "expired": "false"}
    )
    resp.raise_for_status()
    rows = resp.json().get("result") or []
    return [r for r in rows if r.get("is_active") and r.get("state") == "open"]


def select_instruments(
    instruments: list[dict[str, Any]],
    spot: float,
    *,
    max_expiries: int = 2,
    strike_band_pct: float = 0.08,
) -> list[dict[str, Any]]:
    """Nearest expiries, strikes within ±band of spot — keeps WS fan-out small."""
    by_exp: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in instruments:
        by_exp[int(row["expiration_timestamp"])].append(row)
    chosen: list[dict[str, Any]] = []
    for exp in sorted(by_exp)[: max(1, max_expiries)]:
        for row in by_exp[exp]:
            strike = float(row["strike"])
            if spot <= 0 or abs(strike - spot) / spot <= strike_band_pct:
                chosen.append(row)
    return chosen


async def fetch_ticker(
    client: httpx.AsyncClient, base: str, instrument: str
) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/public/ticker"
    resp = await client.get(url, params={"instrument_name": instrument})
    resp.raise_for_status()
    return resp.json().get("result") or {}


async def build_chain_snapshot(
    *,
    currency: str = "BTC",
    base_url: str = DEFAULT_REST,
    max_expiries: int = 2,
    strike_band_pct: float = 0.08,
    timeout: float = 20.0,
    instruments: Optional[list[dict[str, Any]]] = None,
) -> OptionChainSnapshot:
    async with httpx.AsyncClient(timeout=timeout) as client:
        spot = await fetch_index_price(client, base_url, currency)
        if spot <= 0:
            raise RuntimeError("Deribit spot unavailable")
        inst = instruments or await list_active_options(client, base_url, currency)
        selected = select_instruments(
            inst, spot, max_expiries=max_expiries, strike_band_pct=strike_band_pct
        )
        if not selected:
            # widen once
            selected = select_instruments(
                inst, spot, max_expiries=max_expiries, strike_band_pct=0.15
            )
        contracts: list[OptionContract] = []
        # Sequential with small concurrency via gather batches
        batch = 8
        for i in range(0, len(selected), batch):
            chunk = selected[i : i + batch]
            tickers = []
            for row in chunk:
                try:
                    tickers.append(await fetch_ticker(client, base_url, row["instrument_name"]))
                except Exception as exc:  # noqa: BLE001
                    log.debug("ticker miss %s: %s", row.get("instrument_name"), exc)
            for row, ticker in zip(chunk, tickers):
                if not ticker:
                    continue
                expiry = ms_to_dt(row.get("expiration_timestamp"))
                try:
                    contracts.append(parse_option_ticker(ticker, expiry=expiry))
                except Exception as exc:  # noqa: BLE001
                    log.debug("parse miss %s: %s", row.get("instrument_name"), exc)

        # mark_iv as ATM average (percent → fraction already in contracts)
        atm = sorted(contracts, key=lambda c: abs(c.strike - spot))[:4]
        mark_iv = sum(c.iv for c in atm) / len(atm) if atm else None

        snap = OptionChainSnapshot(
            underlying=currency,
            spot=spot,
            contracts=contracts,
            source="deribit-rest-snapshot",
            fetched_at=utcnow(),
            exchange_ts=utcnow(),
            mark_iv=mark_iv,
            dvol=None,
        )
        if contracts:
            snap.validate()
        return snap


def overlay_ticker_on_chain(
    snap: OptionChainSnapshot, ticker: dict[str, Any]
) -> OptionChainSnapshot:
    """Update one contract (and spot if index present) from a WS ticker."""
    name = ticker.get("instrument_name")
    if not name:
        return snap
    index = ticker.get("index_price") or ticker.get("underlying_price")
    spot = float(index) if index else snap.spot
    expiry_by_name = {c.instrument: c.expiry for c in snap.contracts}
    try:
        updated = parse_option_ticker(ticker, expiry=expiry_by_name.get(str(name)))
    except Exception:
        # perpetual / non-option ticker — only refresh spot
        if spot > 0 and spot != snap.spot:
            snap.spot = spot
            snap.fetched_at = utcnow()
            snap.exchange_ts = ms_to_dt(ticker.get("timestamp"))
        return snap

    contracts = []
    found = False
    for c in snap.contracts:
        if c.instrument == updated.instrument:
            contracts.append(updated)
            found = True
        else:
            contracts.append(c)
    if not found and updated.option_type in {"C", "P"}:
        contracts.append(updated)
    snap.contracts = contracts
    if spot > 0:
        snap.spot = spot
    snap.fetched_at = utcnow()
    snap.exchange_ts = ms_to_dt(ticker.get("timestamp"))
    snap.source = "deribit-ws+rest"
    return snap
