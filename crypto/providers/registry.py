"""Discover and instantiate providers by config + feature flags."""

from __future__ import annotations

import logging
from typing import Any, Optional

from crypto.config import get_flag, load_config
from crypto.providers.types import (
    NewsProvider,
    OptionChainProvider,
    PerpProvider,
    ProviderMeta,
)

log = logging.getLogger(__name__)

# Lazy import map: kind → name → factory
_FACTORIES: dict[str, dict[str, type]] = {}


def _register_builtins() -> None:
    if _FACTORIES:
        return
    from crypto.providers.mock.chain import MockOptionChainProvider
    from crypto.providers.mock.perp import MockPerpProvider
    from crypto.providers.mock.news import MockNewsProvider
    from crypto.providers.mock.execution import MockExecutionVenue
    from crypto.providers.deribit.chain import DeribitOptionChainProvider
    from crypto.providers.deribit.perp import DeribitPerpProvider
    from crypto.providers.delta.chain import DeltaOptionChainProvider
    from crypto.providers.delta.execution import DeltaPaperExecutionVenue
    from crypto.providers.delta.live_execution import DeltaLiveExecutionVenue
    from crypto.providers.binance.perp import BinancePerpProvider
    from crypto.providers.cryptopanic.news import CryptoPanicNewsProvider

    _FACTORIES["option_chain"] = {
        "mock": MockOptionChainProvider,
        "deribit": DeribitOptionChainProvider,
        "delta": DeltaOptionChainProvider,
    }
    _FACTORIES["perp"] = {
        "mock": MockPerpProvider,
        "binance": BinancePerpProvider,
        "deribit": DeribitPerpProvider,
    }
    _FACTORIES["news"] = {
        "mock": MockNewsProvider,
        "cryptopanic": CryptoPanicNewsProvider,
    }
    _FACTORIES["execution"] = {
        "mock": MockExecutionVenue,
        "delta": DeltaPaperExecutionVenue,
        "delta_live": DeltaLiveExecutionVenue,
    }


class ProviderRegistry:
    """Config-driven provider selection. Highest priority among enabled+flagged wins."""

    def __init__(self, config: dict[str, Any] | None = None):
        _register_builtins()
        self.config = config or load_config()
        self._instances: dict[tuple[str, str], Any] = {}

    def _metas(self, kind: str) -> list[ProviderMeta]:
        rows = (self.config.get("providers") or {}).get(kind) or []
        out: list[ProviderMeta] = []
        for r in rows:
            out.append(
                ProviderMeta(
                    name=r["name"],
                    kind=kind,
                    enabled=bool(r.get("enabled", False)),
                    priority=int(r.get("priority", 100)),
                    feature_flag=r.get("feature_flag") or f"feeds.{r['name']}",
                    credentials_ref=r.get("credentials_ref"),
                    rate_limits=dict(r.get("rate_limits") or {}),
                )
            )
        return out

    def list_providers(self, kind: str | None = None) -> list[dict[str, Any]]:
        kinds = [kind] if kind else list((self.config.get("providers") or {}).keys())
        result = []
        for k in kinds:
            for m in self._metas(k):
                flag_on = get_flag(m.feature_flag, default=False)
                # feeds.mock defaults true via config; treat missing mock flag as on when enabled
                if m.name == "mock" and m.feature_flag == "feeds.mock":
                    flag_on = get_flag("feeds.mock", default=True)
                result.append(
                    {
                        "kind": m.kind,
                        "name": m.name,
                        "enabled": m.enabled,
                        "priority": m.priority,
                        "feature_flag": m.feature_flag,
                        "flag_on": flag_on,
                        "active_candidate": m.enabled and flag_on,
                        "credentials_ref": m.credentials_ref,
                        "rate_limits": m.rate_limits,
                    }
                )
        return result

    def _pick(self, kind: str) -> Optional[ProviderMeta]:
        candidates = [
            m
            for m in self._metas(kind)
            if m.enabled and get_flag(m.feature_flag, default=(m.name == "mock"))
        ]
        if not candidates:
            # Always fall back to mock if registered
            for m in self._metas(kind):
                if m.name == "mock":
                    return m
            return None
        return sorted(candidates, key=lambda m: m.priority)[0]

    def _instantiate(self, kind: str, name: str) -> Any:
        key = (kind, name)
        if key in self._instances:
            return self._instances[key]
        factories = _FACTORIES.get(kind) or {}
        cls = factories.get(name)
        if cls is None:
            raise KeyError(f"No provider factory for {kind}/{name}")
        inst = cls()
        self._instances[key] = inst
        return inst

    def option_chain(self) -> OptionChainProvider:
        meta = self._pick("option_chain")
        if meta is None:
            raise RuntimeError("No option_chain provider available")
        log.info("option_chain provider → %s", meta.name)
        return self._instantiate("option_chain", meta.name)

    def perp(self) -> PerpProvider:
        meta = self._pick("perp")
        if meta is None:
            raise RuntimeError("No perp provider available")
        log.info("perp provider → %s", meta.name)
        return self._instantiate("perp", meta.name)

    def news(self) -> NewsProvider:
        meta = self._pick("news")
        if meta is None:
            raise RuntimeError("No news provider available")
        return self._instantiate("news", meta.name)

    def execution(self):
        """Paper/demo or live Confirm venue. Falls back to mock sim when exec flags off."""
        meta = self._pick("execution")
        if meta is None:
            # Always have a sim venue available for tests / kill-switch
            return self._instantiate("execution", "mock")
        return self._instantiate("execution", meta.name)

    def active_summary(self) -> dict[str, str]:
        return {
            "option_chain": (self._pick("option_chain") or ProviderMeta("none", "option_chain", False, 0, "")).name,
            "perp": (self._pick("perp") or ProviderMeta("none", "perp", False, 0, "")).name,
            "news": (self._pick("news") or ProviderMeta("none", "news", False, 0, "")).name,
            "execution": (
                self._pick("execution")
                or ProviderMeta("mock", "execution", True, 100, "exec.paper")
            ).name,
        }


_REGISTRY: ProviderRegistry | None = None


def get_registry(*, reload: bool = False) -> ProviderRegistry:
    global _REGISTRY
    if _REGISTRY is None or reload:
        _REGISTRY = ProviderRegistry()
    return _REGISTRY
