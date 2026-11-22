"""Ingest → classify → score → dedupe → merge calendar stubs.

Flag-gated by ``intel.news``. Mock fixtures are the offline default;
CryptoPanic is used when the news provider returns live rows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from crypto.config import get_flag, load_config
from crypto.intel.blackout import blackout_summary
from crypto.intel.calendar import build_calendar_stubs
from crypto.intel.classify import classify_headline, novelty_against, reliability_for_source
from crypto.intel.schema import IntelEvent, event_to_dict, from_news_event_dict
from crypto.intel.score import score_and_tier
from crypto.providers.types import NewsEvent, utcnow

log = logging.getLogger(__name__)


def news_event_to_intel(e: NewsEvent, *, ingested_at: datetime) -> IntelEvent:
    """Lift a provider NewsEvent into the normalized schema (pre-score OK)."""
    return IntelEvent(
        event_id=e.event_id,
        title=e.title,
        source=e.source,
        tier=e.tier or "WATCH",
        score=float(e.score or 0.0),
        event_type=getattr(e, "event_type", None) or "other",
        assets=list(e.assets or []),
        venues=list(getattr(e, "venues", None) or []),
        url=e.url,
        published_at=e.published_at,
        ingested_at=ingested_at,
        occurs_at=getattr(e, "occurs_at", None),
        blackout_start=getattr(e, "blackout_start", None),
        blackout_until=getattr(e, "blackout_until", None),
        novelty=float(getattr(e, "novelty", 1.0) or 1.0),
        source_reliability=float(getattr(e, "source_reliability", 0.0) or 0.0),
        prior_move_proxy=float(getattr(e, "prior_move_proxy", 0.0) or 0.0),
        market_confirmation=float(getattr(e, "market_confirmation", 0.0) or 0.0),
        impact_note=e.impact_note or "",
        engine_action=str(getattr(e, "engine_action", "") or ""),
        raw=dict(getattr(e, "raw", None) or {}),
    )


def intel_to_news_event(e: IntelEvent) -> NewsEvent:
    """Down-cast for store writer / legacy callers."""
    return NewsEvent(
        event_id=e.event_id,
        title=e.title,
        source=e.source,
        tier=e.tier,
        score=e.score,
        assets=list(e.assets),
        url=e.url,
        published_at=e.published_at,
        impact_note=e.impact_note,
        event_type=e.event_type,
        venues=list(e.venues),
        occurs_at=e.occurs_at,
        blackout_start=e.blackout_start,
        blackout_until=e.blackout_until,
        novelty=e.novelty,
        source_reliability=e.source_reliability,
        prior_move_proxy=e.prior_move_proxy,
        market_confirmation=e.market_confirmation,
        engine_action=e.engine_action,
        raw=dict(e.raw),
    )


def enrich_event(event: IntelEvent, *, seen_titles: list[str], now: datetime) -> IntelEvent:
    """Classify + score one event.

    When ``intel.gemma`` is on, try local Gemma first; timeout / invalid JSON
    falls back to keyword rules. Gemma never creates a GO.
    """
    if get_flag("intel.gemma", default=False):
        from crypto.gemma.classify import classify_with_gemma

        classified = classify_with_gemma(
            event.title,
            source=event.source,
            assets_hint=event.assets,
            venues_hint=event.venues,
            event_type_hint=event.event_type,
        )
        event.raw = dict(event.raw or {})
        event.raw["classify_source"] = classified.get("source")
        event.raw["classify_confidence"] = classified.get("confidence")
    else:
        classified = classify_headline(
            event.title,
            assets_hint=event.assets,
            venues_hint=event.venues,
            event_type_hint=event.event_type,
        )
    event.event_type = str(classified["event_type"])
    event.assets = list(classified["assets"])  # type: ignore[arg-type]
    event.venues = list(classified["venues"])  # type: ignore[arg-type]
    if not event.prior_move_proxy:
        event.prior_move_proxy = float(classified["prior_move_proxy"])  # type: ignore[arg-type]
    if not event.source_reliability:
        event.source_reliability = reliability_for_source(event.source)
    event.novelty = novelty_against(event.title, seen_titles)
    return score_and_tier(event, now)


def merge_and_dedupe(events: list[IntelEvent]) -> list[IntelEvent]:
    """Keep first event_id; drop near-duplicate titles (novelty already applied)."""
    by_id: dict[str, IntelEvent] = {}
    for e in events:
        if e.event_id not in by_id:
            by_id[e.event_id] = e
    return list(by_id.values())


_SHARED_EVENT_TYPES = {"macro", "maintenance", "regulation"}


def event_relevant_to_asset(event: IntelEvent, underlying: str) -> bool:
    """True if the event tags the active asset, is untagged, or is shared macro."""
    u = (underlying or "").strip().upper()
    assets = [a.upper() for a in (event.assets or [])]
    if not assets:
        return True
    if u and u in assets:
        return True
    if (event.event_type or "").lower() in _SHARED_EVENT_TYPES:
        return True
    return False


def filter_events_for_asset(
    events: list[IntelEvent], underlying: str
) -> list[IntelEvent]:
    return [e for e in events if event_relevant_to_asset(e, underlying)]


async def run_intel_pipeline(
    news_provider: Any | None,
    *,
    now: datetime | None = None,
    include_calendar: bool = True,
    include_near_blackout: bool = False,
    limit: int = 40,
    underlying: str | None = None,
) -> dict[str, Any]:
    """Fetch provider events, merge calendar stubs, classify, score, blackout.

    Returns ``{events, blackout, provider}``. Empty when ``intel.news`` is off.
    When ``underlying`` is set, blackout uses asset-relevant events only.
    """
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if not get_flag("intel.news", default=True):
        return {
            "events": [],
            "blackout": {
                "active": False,
                "reasons": [],
                "window_minutes": 30,
                "blackout_until": None,
                "note": "intel.news flag off",
            },
            "provider": None,
        }

    cfg = load_config()
    intel_cfg = cfg.get("intel") or {}
    blackout_window = int(intel_cfg.get("blackout_window_minutes") or 30)
    active_u = (underlying or cfg.get("underlying") or "BTC").strip().upper()

    raw_events: list[IntelEvent] = []
    provider_name = None
    if news_provider is not None:
        provider_name = getattr(news_provider, "name", type(news_provider).__name__)
        try:
            fetched = await news_provider.fetch_events(limit=limit)
            for item in fetched:
                if isinstance(item, NewsEvent):
                    raw_events.append(news_event_to_intel(item, ingested_at=now))
                elif isinstance(item, IntelEvent):
                    item.ingested_at = item.ingested_at or now
                    raw_events.append(item)
                elif isinstance(item, dict):
                    raw_events.append(from_news_event_dict(item, ingested_at=now))
        except Exception as exc:  # fail soft — calendar still works
            log.warning("news provider fetch failed: %s", exc)

    calendar_on = include_calendar and bool(intel_cfg.get("calendar_stubs", True))
    if calendar_on and get_flag("intel.calendar", default=True):
        raw_events.extend(
            build_calendar_stubs(now, include_near_blackout=include_near_blackout)
        )

    seen: list[str] = []
    scored: list[IntelEvent] = []
    for e in raw_events:
        enriched = enrich_event(e, seen_titles=seen, now=now)
        seen.append(enriched.title)
        scored.append(enriched)

    scored = merge_and_dedupe(scored)
    # Sort: IMMINENT → BREAKING → WATCH, then score desc
    tier_rank = {"IMMINENT": 0, "BREAKING": 1, "WATCH": 2}
    scored.sort(key=lambda x: (tier_rank.get(x.tier, 9), -x.score))
    scored = scored[:limit]

    blackout_pool = filter_events_for_asset(scored, active_u)
    blackout = blackout_summary(blackout_pool, now, window_minutes=blackout_window)
    return {
        "events": scored,
        "blackout": blackout,
        "provider": provider_name,
        "underlying": active_u,
    }


def events_for_api(events: list[IntelEvent]) -> list[dict[str, Any]]:
    return [event_to_dict(e) for e in events]
