"""Orchestrate providers → engine → durable store for one cockpit snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from crypto.config import get_flag, list_flags, load_config, set_flag
from crypto.engine.gex import compute_gex_features
from crypto.engine.inference import run_inference
from crypto.engine.orderflow import OrderflowState, compute_orderflow_features
from crypto.engine.signals import build_signal_card
from crypto.engine.vetoes import stub_risk_state
from crypto.feeds.manager import live_feeds_requested
from crypto.feeds.state import get_feed_state
from crypto.feeds.stale import STALE_THRESHOLD_S, age_seconds, is_stale
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
        underlying = cfg.get("underlying") or "BTC"
        feed = get_feed_state()

        chain_p = self.registry.option_chain()
        perp_p = self.registry.perp()
        snap = await chain_p.fetch_chain(underlying)
        perp = await perp_p.fetch_perp(f"{underlying}-PERP")

        self.store.write_chain(snap)

        # Signed-tape flow inventory from live Deribit trades (when present)
        flow_inv = feed.get_flow_inventory()
        use_inv = flow_inv if flow_inv.total_trades > 0 else None
        gex = compute_gex_features(snap, flow_inventory=use_inv)
        self.store.write_gex(gex)

        # Append recent option/perp prints into orderflow rolling state
        if feed.recent_trades:
            self._orderflow_state.append_trades(feed.recent_trades[-200:])
        orderflow = compute_orderflow_features(perp, state=self._orderflow_state)
        self.store.write_orderflow_features(orderflow)

        news_events = []
        if get_flag("intel.news", default=True):
            news_p = self.registry.news()
            news_events = await news_p.fetch_events()
            self.store.write_events(news_events)

        blackout = any(e.tier == "IMMINENT" and e.score >= 0.9 for e in news_events)
        src = snap.source or ""
        live_source = (
            "deribit" in src
            and "stub" not in src
            and "fixture" not in src
            and "mock" not in src
        )
        live_path = bool(get_flag("feeds.deribit") and (live_feeds_requested() or live_source))
        card = build_signal_card(
            gex,
            perp,
            orderflow=orderflow,
            news_blackout=blackout,
            chain_fetched_at=snap.fetched_at,
            live_signal_path=live_path,
        )
        self.store.write_signal(card)

        inference = run_inference(gex, orderflow)

        chain_stale = is_stale(
            snap.fetched_at,
            threshold_s=float((cfg.get("feeds") or {}).get("stale_threshold_s") or STALE_THRESHOLD_S),
        )

        return {
            "brand": "CryptoCubera",
            "mode": cfg.get("mode") or "advisory",
            "underlying": underlying,
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
            },
            "feeds": feed.status(),
            "gex": _serialize_gex(gex) if get_flag("ui.gex_map", default=True) else None,
            "orderflow": _serialize_orderflow(orderflow)
            if get_flag("ui.regime_strip", default=True)
            else None,
            "signal": _serialize_card(card) if get_flag("ui.go_cards", default=True) else None,
            "inference": inference,
            "news": [
                {
                    "event_id": e.event_id,
                    "title": e.title,
                    "source": e.source,
                    "tier": e.tier,
                    "score": e.score,
                    "assets": e.assets,
                    "impact_note": e.impact_note,
                    "published_at": e.published_at.isoformat() if e.published_at else None,
                }
                for e in news_events
            ]
            if get_flag("intel.news", default=True)
            else [],
            "execution": {
                "can_trade": False,
                "kill_switch_armed": False,
                "note": "Advisory only. Kill switch disabled until execution is wired.",
            },
        }

    def toggle_flag(self, name: str, value: bool, actor: str = "operator") -> dict[str, Any]:
        result = set_flag(name, value, actor=actor)
        self.store.audit_flag(name, result["previous"], result["value"], actor)
        # Refresh registry so provider picks respect new flags
        self.registry = get_registry(reload=True)
        return result


_SERVICE: CockpitService | None = None


def get_service() -> CockpitService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = CockpitService()
    return _SERVICE
