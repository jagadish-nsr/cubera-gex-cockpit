"""Orchestrate providers → engine → durable store for one cockpit snapshot.

Hot path (CRY-21): ``snapshot()`` and ``switch_underlying()`` serve an
in-memory per-asset cache with no I/O — target sub-millisecond compute.
Background tasks refresh caches, persist to SQLite, and realign live feeds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from crypto.cache.snapshot_cache import SnapshotCache
from crypto.config import (
    get_active_underlying,
    get_asset_config,
    get_flag,
    is_asset_enabled,
    list_assets,
    list_flags,
    load_config,
    set_flag,
    set_underlying,
)
from crypto.engine.flow_alerts import FlowAlertState, evaluate_flow_alerts
from crypto.engine.gex import compute_gex_features
from crypto.engine.inference import run_inference
from crypto.engine.orderflow import OrderflowState, compute_orderflow_features
from crypto.engine.signals import build_signal_card
from crypto.engine.vetoes import stub_risk_state
from crypto.feeds.manager import is_live_market_data_source
from crypto.feeds.state import get_feed_state
from crypto.feeds.stale import STALE_THRESHOLD_S, age_seconds, is_stale
from crypto.intel.further_reading import further_reading_links
from crypto.intel.pipeline import (
    events_for_api,
    filter_events_for_asset,
    intel_to_news_event,
    run_intel_pipeline,
)
from crypto.providers.registry import get_registry
from crypto.store.writer import StoreWriter

log = logging.getLogger(__name__)

# Sub-ms budget is for pure memory hits; cold builds are measured separately.
_SUB_MS_BUDGET_MS = 1.0
# Absolute ceiling: never serve a cache entry older than this (forces rebuild).
_HARD_CACHE_MAX_AGE_S = 30.0


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


def _timing(compute_ms: float, *, cache_hit: bool, path: str) -> dict[str, Any]:
    return {
        "compute_ms": round(compute_ms, 4),
        "cache_hit": cache_hit,
        "path": path,
        "sub_ms": compute_ms < _SUB_MS_BUDGET_MS,
        "note": "compute_ms excludes HTTP/JSON serialization RTT",
    }


def _parse_iso_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _stale_threshold_s() -> float:
    cfg = load_config()
    return float((cfg.get("feeds") or {}).get("stale_threshold_s") or STALE_THRESHOLD_S)


class CockpitService:
    def __init__(self) -> None:
        self.store = StoreWriter()
        self.registry = get_registry()
        self.snapshot_cache = SnapshotCache()
        self._orderflow_by_asset: dict[str, OrderflowState] = {}
        # CryptoCubera flow-alert cooldowns per underlying (never equity Sentinel).
        self._flow_alert_by_asset: dict[str, FlowAlertState] = {}
        self._bg_tasks: set[asyncio.Task] = set()
        self._prewarm_done = False
        self._realign_task: asyncio.Task | None = None
        self._realign_generation = 0
        self._refreshing: set[str] = set()

    @property
    def _orderflow_state(self) -> OrderflowState:
        """Active-asset orderflow (compat for callers expecting a single state)."""
        u = get_active_underlying()
        return self._orderflow_by_asset.setdefault(u, OrderflowState())

    def _flow_alert_state(self, underlying: str) -> FlowAlertState:
        u = (underlying or "").strip().upper()
        return self._flow_alert_by_asset.setdefault(u, FlowAlertState())

    def _track(self, task: asyncio.Task) -> None:
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _spawn(self, coro) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._track(loop.create_task(coro))

    def _apply_cache_freshness(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Recompute live age/stale on a cached payload; never serve a live GO when stale.

        Sub-ms: pure memory. Schedules a background refresh when data is aging or stale.
        """
        underlying = str(payload.get("underlying") or get_active_underlying()).upper()
        thr = _stale_threshold_s()
        chain = dict(payload.get("chain") or {})
        fetched = _parse_iso_dt(chain.get("fetched_at"))
        age = age_seconds(fetched)
        stale = is_stale(fetched, threshold_s=thr)
        chain["age_s"] = age
        chain["stale"] = stale
        payload["chain"] = chain

        src = chain.get("source") or (payload.get("gex") or {}).get("source") or ""
        live = is_live_market_data_source(src)
        freshness: dict[str, Any] = {
            "age_s": round(age, 4) if age != float("inf") else None,
            "stale": stale,
            "live_source": live,
            "threshold_s": thr,
            "refresh_scheduled": False,
        }

        if stale and live:
            # Hard veto above models: do not leave a cached GO actionable.
            sig = payload.get("signal")
            if isinstance(sig, dict):
                sig = dict(sig)
                note = f"Cached chain stale ({age:.1f}s > {thr}s); refresh scheduled."
                if sig.get("status") == "GO":
                    sig["status"] = "NO-GO"
                reasons = [note] + [r for r in (sig.get("reasons") or []) if r != note]
                sig["reasons"] = reasons[:5]
                vetoes = list(sig.get("veto_reasons") or [])
                if note not in vetoes:
                    vetoes.insert(0, note)
                sig["veto_reasons"] = vetoes[:8]
                payload["signal"] = sig
            freshness["forced_nogo"] = True
            freshness["refresh_scheduled"] = True
            if underlying not in self._refreshing:
                self._spawn(self.refresh_snapshot_cache(underlying, persist=True))
        elif age > thr * 0.5:
            freshness["refresh_scheduled"] = True
            if underlying not in self._refreshing:
                self._spawn(self.refresh_snapshot_cache(underlying, persist=True))

        payload["cache_freshness"] = freshness
        return payload

    async def snapshot(self) -> dict[str, Any]:
        """Return cockpit payload for the active underlying (cache-first, no I/O)."""
        t0 = time.perf_counter()
        underlying = get_active_underlying()
        cached = self.snapshot_cache.get(underlying, max_age_s=_HARD_CACHE_MAX_AGE_S)
        if cached is not None:
            # Defense: never serve a body tagged for another asset.
            if str(cached.get("underlying") or "").upper() != underlying:
                self.snapshot_cache.invalidate(underlying)
            else:
                cached = self._apply_cache_freshness(cached)
                cached["timing"] = _timing(
                    (time.perf_counter() - t0) * 1000.0,
                    cache_hit=True,
                    path="snapshot_cache",
                )
                return cached

        # Cold path: build once, cache, return (may include I/O + store).
        payload = await self._build_snapshot(underlying, persist=True)
        build_ms = (time.perf_counter() - t0) * 1000.0
        self.snapshot_cache.put(underlying, payload, build_ms=build_ms)
        out = dict(payload)
        out["timing"] = _timing(build_ms, cache_hit=False, path="snapshot_cold_build")
        return out

    async def _build_snapshot(self, underlying: str, *, persist: bool) -> dict[str, Any]:
        """Full snapshot build for one underlying (I/O + optional SQLite persist)."""
        cfg = load_config()
        asset = get_asset_config(underlying)
        feed = get_feed_state()

        chain_p = self.registry.option_chain()
        perp_p = self.registry.perp()
        snap = await chain_p.fetch_chain(underlying)
        perp_symbol = str(asset.get("deribit_perp") or f"{underlying}-PERPETUAL")
        perp = await perp_p.fetch_perp(perp_symbol)

        if persist:
            self.store.write_chain(snap)

        flow_inv = feed.get_flow_inventory()
        # Only use flow inventory when it belongs to this underlying's tape.
        use_inv = None
        if flow_inv.total_trades > 0 and (
            feed.active_underlying is None or feed.active_underlying == underlying
        ):
            use_inv = flow_inv
        gex = compute_gex_features(snap, flow_inventory=use_inv)
        if persist:
            self.store.write_gex(gex)

        of_state = self._orderflow_by_asset.setdefault(underlying, OrderflowState())
        if feed.recent_trades and (
            feed.active_underlying is None or feed.active_underlying == underlying
        ):
            of_state.append_trades(feed.recent_trades[-200:])
        orderflow = compute_orderflow_features(
            perp, state=of_state, underlying=underlying
        )
        if persist:
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
        display_events = filter_events_for_asset(intel_events, underlying)
        blackout_info = intel["blackout"]
        if persist and intel_events:
            self.store.write_events([intel_to_news_event(e) for e in intel_events])

        blackout = bool(blackout_info.get("active"))
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

            card = explain_go_card(card)
        if persist:
            self.store.write_signal(card)

        inference = run_inference(gex, orderflow)

        flow_alerts_on = bool(get_flag("ui.flow_alerts", default=True))
        flow_alerts: list[dict[str, Any]] = []
        if flow_alerts_on:
            flow_alerts = evaluate_flow_alerts(
                gex,
                orderflow,
                underlying=underlying,
                state=self._flow_alert_state(underlying),
                apply_cooldown=True,
            )

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
                "currency": asset.get("currency"),
                "deribit_perp": asset.get("deribit_perp"),
                "delta_symbols": list(asset.get("delta_symbols") or []),
            },
            "currency": asset.get("currency"),
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
            "flow_alerts": flow_alerts if flow_alerts_on else [],
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
                "desk": "CryptoCubera",
                "scope": "crypto",
                "further_reading": further_reading_links() if intel_flag else [],
            },
            "execution": exec_status,
            "paper_trades_open": open_trades,
        }

    async def prewarm_snapshots(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Build caches for enabled assets so the first UI click is a memory hit."""
        want = symbols or [a["symbol"] for a in list_assets() if a.get("enabled")]
        out: dict[str, Any] = {}
        for sym in want:
            if not is_asset_enabled(sym):
                continue
            t0 = time.perf_counter()
            # Persist off the prewarm critical section — still write in build for recording.
            payload = await self._build_snapshot(sym, persist=True)
            build_ms = (time.perf_counter() - t0) * 1000.0
            self.snapshot_cache.put(sym, payload, build_ms=build_ms)
            out[sym] = {"build_ms": round(build_ms, 3), "spot": payload.get("spot")}
            log.info("Snapshot cache prewarm %s in %.2fms spot=%s", sym, build_ms, payload.get("spot"))
        self._prewarm_done = True
        return out

    async def refresh_snapshot_cache(
        self,
        underlying: str,
        *,
        persist: bool = True,
        generation: int | None = None,
    ) -> None:
        """Background rebuild for one asset (live upgrade after switch)."""
        want = (underlying or "").strip().upper()
        if want in self._refreshing and generation is None:
            return
        self._refreshing.add(want)
        try:
            if generation is not None and generation != self._realign_generation:
                return
            t0 = time.perf_counter()
            payload = await self._build_snapshot(want, persist=persist)
            if str(payload.get("underlying") or "").upper() != want:
                log.warning(
                    "Dropping refresh with mismatched underlying want=%s got=%s",
                    want,
                    payload.get("underlying"),
                )
                return
            build_ms = (time.perf_counter() - t0) * 1000.0
            self.snapshot_cache.put(want, payload, build_ms=build_ms)
        except Exception as exc:  # noqa: BLE001
            log.warning("Background snapshot refresh failed for %s: %s", want, exc)
        finally:
            self._refreshing.discard(want)

    async def _bg_realign_feeds(self, underlying: str, generation: int) -> None:
        """Restart collectors for the new asset without blocking the switcher.

        Debounced: a newer switch bumps ``_realign_generation`` so stale
        realigns exit before doing heavy I/O.
        """
        # Brief yield so the HTTP response can flush before feed I/O.
        await asyncio.sleep(0)
        if generation != self._realign_generation:
            return
        from crypto.feeds.manager import get_feed_manager, live_feeds_requested

        mgr = get_feed_manager()
        feed = get_feed_state()
        feed.set_active_underlying(underlying)
        try:
            if generation != self._realign_generation:
                return
            if mgr._started or live_feeds_requested():
                await mgr.restart_for_underlying(underlying)
            else:
                await mgr.prime_chain_for_underlying(underlying, prefer_live=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("Background feed realign failed for %s: %s", underlying, exc)
        if generation != self._realign_generation:
            return
        await self.refresh_snapshot_cache(
            underlying, persist=True, generation=generation
        )

    def toggle_flag(self, name: str, value: bool, actor: str = "operator") -> dict[str, Any]:
        result = set_flag(name, value, actor=actor)
        self.store.audit_flag(name, result["previous"], result["value"], actor)
        self.registry = get_registry(reload=True)
        # Flag flips can change providers / UI sections — drop caches.
        self.snapshot_cache.invalidate()
        return result

    async def switch_underlying(self, symbol: str, actor: str = "operator") -> dict[str, Any]:
        """Switch active asset in memory; return cached snapshot (sub-ms when warm).

        Feed restart + cache refresh run in the background. Manual Decision /
        live_place / auto flags are untouched.
        """
        t0 = time.perf_counter()
        result = set_underlying(symbol, actor=actor)
        want = result["underlying"]
        # Memory-only: drop prior-asset live chain so a BTC book cannot be read
        # as ETH. Snapshot UI still comes from SnapshotCache (already warm).
        get_feed_state().clear_for_underlying_switch(underlying=want)

        cached = self.snapshot_cache.get(want, max_age_s=_HARD_CACHE_MAX_AGE_S)
        cache_hit = cached is not None and str(cached.get("underlying") or "").upper() == want
        if not cache_hit:
            # Rare cold path (prewarm missed / wrong-asset / hard TTL): build without
            # awaiting feed restart.
            cached = await self._build_snapshot(want, persist=False)
            build_ms = (time.perf_counter() - t0) * 1000.0
            self.snapshot_cache.put(want, cached, build_ms=build_ms)
            cache_hit = False
        else:
            cached = self._apply_cache_freshness(cached)

        compute_ms = (time.perf_counter() - t0) * 1000.0
        result["spot"] = cached.get("spot")
        result["chain_source"] = (cached.get("chain") or {}).get("source")
        result["snapshot"] = cached
        result["timing"] = _timing(
            compute_ms,
            cache_hit=cache_hit,
            path="switch_cache" if cache_hit else "switch_cold_build",
        )
        result["feeds"] = {
            "restarted": False,
            "scheduled": True,
            "note": "feed realign + cache refresh scheduled in background",
        }

        # Background: debounced feed realign + cache refresh (I/O off hot path).
        self._realign_generation += 1
        gen = self._realign_generation
        if self._realign_task and not self._realign_task.done():
            self._realign_task.cancel()
        try:
            loop = asyncio.get_running_loop()
            self._realign_task = loop.create_task(self._bg_realign_feeds(want, gen))
            self._track(self._realign_task)
        except RuntimeError:
            pass
        return result


_SERVICE: CockpitService | None = None


def get_service() -> CockpitService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = CockpitService()
    return _SERVICE
