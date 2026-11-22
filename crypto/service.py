"""Orchestrate providers → engine → durable store for one cockpit snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from crypto.config import (
    get_active_underlying,
    get_asset_config,
    get_flag,
    list_assets,
    list_flags,
    load_config,
    set_flag,
    set_underlying,
)
from crypto.engine.gex import compute_gex_features
from crypto.engine.inference import run_inference
from crypto.engine.orderflow import OrderflowState, compute_orderflow_features
from crypto.engine.signals import build_signal_card
from crypto.engine.vetoes import stub_risk_state
from crypto.feeds.manager import is_live_market_data_source
from crypto.feeds.state import get_feed_state
from crypto.feeds.stale import STALE_THRESHOLD_S, age_seconds, is_stale
from crypto.intel.pipeline import (
    events_for_api,
    filter_events_for_asset,
    intel_to_news_event,
    run_intel_pipeline,
)
from crypto.providers.registry import get_registry
from crypto.store.writer import StoreWriter


def _ser_dt(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _serialize_card(card: dict[str, Any]) -> dict[str, Any]:
    out = dict(card)
    for k in ("created_at", "valid_until"):
        if k in out:
            out[k] = _ser_dt(out[k])
    return out


def _serialize_gex(gex: dict[str, Any]) -> dict[str, Any]:
    out = dict(gex)
    out["computed_at"] = _ser_dt(out.get("computed_at"))
    return out


def _serialize_orderflow(of: dict[str, Any]) -> dict[str, Any]:
    out = dict(of)
    out["computed_at"] = _ser_dt(out.get("computed_at"))
    return out


class CockpitService:
    def __init__(self) -> None:
        self.store = StoreWriter()
        self.registry = get_registry()
        self._orderflow_state = OrderflowState()

    async def snapshot(self) -> dict[str, Any]:
        cfg = load_config()
        underlying = get_active_underlying()
        asset = get_asset_config(underlying)
        feed = get_feed_state()

        chain_p = self.registry.option_chain()
        perp_p = self.registry.perp()
        snap = await chain_p.fetch_chain(underlying)
        perp_symbol = str(asset.get("deribit_perp") or f"{underlying}-PERPETUAL")
        perp = await perp_p.fetch_perp(perp_symbol)

        self.store.write_chain(snap)

        # Signed-tape flow inventory from live Deribit trades (when present)
        flow_inv = feed.get_flow_inventory()
        use_inv = flow_inv if flow_inv.total_trades > 0 else None
        gex = compute_gex_features(snap, flow_inventory=use_inv)
        self.store.write_gex(gex)

        # Append recent option/perp prints into orderflow rolling state
        if feed.recent_trades:
            self._orderflow_state.append_trades(feed.recent_trades[-200:])
        orderflow = compute_orderflow_features(
            perp, state=self._orderflow_state, underlying=underlying
        )
        self.store.write_orderflow_features(orderflow)

        intel_flag = get_flag("intel.news", default=True)
        news_p = self.registry.news() if intel_flag else None
        include_cal = bool(get_flag("intel.calendar", default=True))
        intel = await run_intel_pipeline(
            news_p,
            include_calendar=include_cal and intel_flag,
            underlying=underlying,
        )
        intel_events = intel["events"]
        # Display feed filtered to active asset; blackout already asset-aware
        display_events = filter_events_for_asset(intel_events, underlying)
        blackout_info = intel["blackout"]
        if intel_events:
            self.store.write_events([intel_to_news_event(e) for e in intel_events])

        blackout = bool(blackout_info.get("active"))
        # Real live MD only — stub/fixture/mock soft-fallback keeps mock stale rules
        # even when CRYPTO_LIVE_FEEDS=1 (WS/REST miss must not 2s-veto the paper desk).
        live_path = bool(
            get_flag("feeds.deribit") and is_live_market_data_source(snap.source)
        )
        card = build_signal_card(
            gex,
            perp,
            orderflow=orderflow,
            news_blackout=blackout,
            chain_fetched_at=snap.fetched_at,
            live_signal_path=live_path,
        )
        if get_flag("intel.gemma", default=False):
            from crypto.gemma.go_prose import explain_go_card

            # Language only — never flips NO-GO → GO or invents levels.
            card = explain_go_card(card)
        self.store.write_signal(card)

        inference = run_inference(gex, orderflow)

        chain_stale = is_stale(
            snap.fetched_at,
            threshold_s=float((cfg.get("feeds") or {}).get("stale_threshold_s") or STALE_THRESHOLD_S),
        )

        from crypto.exec.service import get_execution_service

        exec_svc = get_execution_service()
        exec_status = await exec_svc.status()
        exec_mode = exec_status.get("exec_mode") or {}
        open_trades = (
            exec_svc.ledger.open_intents()[:5]
            if (get_flag("exec.paper") or get_flag("exec.live"))
            else []
        )

        signal_out = None
        if get_flag("ui.go_cards", default=True):
            signal_out = _serialize_card(card)
            signal_out["exec_mode"] = exec_mode.get("mode") or "advisory"
            signal_out["can_confirm"] = bool(exec_mode.get("can_confirm"))
            signal_out["paper_status"] = {
                "open_intents": exec_status.get("open_intents") or 0,
                "adapter": exec_status.get("adapter"),
                "latest_open": open_trades[0] if open_trades else None,
            }

        return {
            "brand": "CryptoCubera",
            "mode": exec_mode.get("mode") or cfg.get("mode") or "advisory",
            "mode_badge": exec_mode.get("badge") or "Advisory",
            "underlying": underlying,
            "asset": {
                "symbol": underlying,
                "label": asset.get("label") or underlying,
                "deribit_perp": asset.get("deribit_perp"),
                "delta_symbols": list(asset.get("delta_symbols") or []),
            },
            "assets": list_assets(),
            "spot": snap.spot,
            "providers": self.registry.active_summary(),
            "provider_catalog": self.registry.list_providers(),
            "flags": list_flags(),
            "engine": {
                "model_version": (cfg.get("engine") or {}).get("model_version") or "rules-v1",
                "rules_version": (cfg.get("engine") or {}).get("rules_version") or "setups-v1",
                "veto_stubs": stub_risk_state(),
            },
            "chain": {
                "source": snap.source,
                "spot": snap.spot,
                "mark_iv": snap.mark_iv,
                "dvol": snap.dvol,
                "n_contracts": len(snap.contracts),
                "fetched_at": snap.fetched_at.isoformat(),
                "age_s": age_seconds(snap.fetched_at),
                "stale": chain_stale,
                "underlying": underlying,
            },
            "feeds": feed.status(),
            "gex": _serialize_gex(gex) if get_flag("ui.gex_map", default=True) else None,
            "orderflow": _serialize_orderflow(orderflow)
            if get_flag("ui.regime_strip", default=True)
            else None,
            "signal": signal_out,
            "inference": inference,
            "news": events_for_api(display_events) if intel_flag else [],
            "intel": {
                "provider": intel.get("provider"),
                "blackout": blackout_info,
                "n_events": len(display_events),
                "flag": intel_flag,
                "underlying": underlying,
                "gemma": get_flag("intel.gemma", default=False),
            },
            "execution": exec_status,
            "paper_trades_open": open_trades,
        }

    def toggle_flag(self, name: str, value: bool, actor: str = "operator") -> dict[str, Any]:
        result = set_flag(name, value, actor=actor)
        self.store.audit_flag(name, result["previous"], result["value"], actor)
        # Refresh registry so provider picks respect new flags
        self.registry = get_registry(reload=True)
        return result

    def switch_underlying(self, symbol: str, actor: str = "operator") -> dict[str, Any]:
        result = set_underlying(symbol, actor=actor)
        # Clear per-asset tape state so ETH/SOL don't inherit BTC CVD
        self._orderflow_state = OrderflowState()
        return result


_SERVICE: CockpitService | None = None


def get_service() -> CockpitService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = CockpitService()
    return _SERVICE
