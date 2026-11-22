"""Orchestrate providers → engine → durable store for one cockpit snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from crypto.config import get_flag, list_flags, load_config, set_flag
from crypto.engine.gex import compute_gex_features
from crypto.engine.signals import build_signal_card
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


class CockpitService:
    def __init__(self) -> None:
        self.store = StoreWriter()
        self.registry = get_registry()

    async def snapshot(self) -> dict[str, Any]:
        cfg = load_config()
        underlying = cfg.get("underlying") or "BTC"

        chain_p = self.registry.option_chain()
        perp_p = self.registry.perp()
        snap = await chain_p.fetch_chain(underlying)
        perp = await perp_p.fetch_perp(f"{underlying}-PERP")

        self.store.write_chain(snap)
        self.store.write_orderflow(perp)

        gex = compute_gex_features(snap)
        self.store.write_gex(gex)

        news_events = []
        if get_flag("intel.news", default=True):
            news_p = self.registry.news()
            news_events = await news_p.fetch_events()
            self.store.write_events(news_events)

        blackout = any(e.tier == "IMMINENT" and e.score >= 0.9 for e in news_events)
        card = build_signal_card(gex, perp, news_blackout=blackout)
        self.store.write_signal(card)

        return {
            "brand": "CryptoCubera",
            "mode": cfg.get("mode") or "advisory",
            "underlying": underlying,
            "spot": snap.spot,
            "providers": self.registry.active_summary(),
            "provider_catalog": self.registry.list_providers(),
            "flags": list_flags(),
            "chain": {
                "source": snap.source,
                "spot": snap.spot,
                "mark_iv": snap.mark_iv,
                "dvol": snap.dvol,
                "n_contracts": len(snap.contracts),
                "fetched_at": snap.fetched_at.isoformat(),
            },
            "gex": _serialize_gex(gex) if get_flag("ui.gex_map", default=True) else None,
            "orderflow": {
                "venue": perp.venue,
                "symbol": perp.symbol,
                "mark": perp.mark,
                "index": perp.index,
                "funding_rate": perp.funding_rate,
                "open_interest": perp.open_interest,
                "cvd_1m": perp.cvd_1m,
                "cvd_5m": perp.cvd_5m,
                "cvd_15m": perp.cvd_15m,
                "book_imbalance": perp.book_imbalance,
                "source": perp.source,
                "fetched_at": perp.fetched_at.isoformat(),
            }
            if get_flag("ui.regime_strip", default=True)
            else None,
            "signal": _serialize_card(card) if get_flag("ui.go_cards", default=True) else None,
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
