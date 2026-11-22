"""CryptoPanic news provider.

Default: mock fixtures (offline UI). Live HTTP when ``CRYPTOPANIC_TOKEN``
is set; fails soft back to mock on network/API errors.

Attribution: CryptoPanic free/paid tiers may require a credit line —
we surface ``powered_by`` on each live event.
API shape: ``GET https://cryptopanic.com/api/{plan}/v2/posts/?auth_token=…``
with a v1 fallback. Plan path defaults to ``developer`` (override via
``CRYPTOPANIC_API_PLAN``).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from crypto.config import get_active_underlying, get_asset_config, load_config
from crypto.providers.mock.news import MockNewsProvider
from crypto.providers.types import NewsEvent

log = logging.getLogger(__name__)

DEFAULT_BASE = "https://cryptopanic.com/api"
DEFAULT_PLAN = "developer"  # path segment; accounts may need growth/enterprise


class CryptoPanicNewsProvider:
    name = "cryptopanic"

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        plan: Optional[str] = None,
        timeout_s: float = 8.0,
    ) -> None:
        self.token = (token if token is not None else os.environ.get("CRYPTOPANIC_TOKEN") or "").strip()
        self.plan = (plan or os.environ.get("CRYPTOPANIC_API_PLAN") or DEFAULT_PLAN).strip()
        self.timeout_s = timeout_s
        self._fallback = MockNewsProvider()

    @property
    def live_enabled(self) -> bool:
        return bool(self.token)

    def _currencies_param(self) -> str:
        cfg = load_config()
        intel = (cfg.get("intel") or {}).get("cryptopanic") or {}
        asset = get_asset_config(get_active_underlying())
        per_asset = str(asset.get("cryptopanic_currencies") or "").strip()
        if per_asset:
            return per_asset
        return str(intel.get("currencies") or "BTC,ETH,SOL")

    async def fetch_events(self, limit: int = 20) -> list[NewsEvent]:
        if not self.live_enabled:
            events = await self._fallback.fetch_events(limit)
            for e in events:
                e.source = e.source if e.source.startswith("fixture") else f"mock/{e.source}"
                e.raw = {**(e.raw or {}), "provider": "cryptopanic", "mode": "mock_fallback"}
            return events

        try:
            live = await self._fetch_live(limit=limit)
            if live:
                return live
            log.warning("CryptoPanic returned no rows; using mock fixtures")
        except Exception as exc:
            log.warning("CryptoPanic live fetch failed (%s); using mock fixtures", exc)

        events = await self._fallback.fetch_events(limit)
        for e in events:
            e.source = f"cryptopanic-fallback/{e.source}"
            e.raw = {**(e.raw or {}), "provider": "cryptopanic", "mode": "error_fallback"}
        return events

    async def _fetch_live(self, limit: int = 20) -> list[NewsEvent]:
        # Try v2 plan path first, then legacy v1.
        urls = [
            f"{DEFAULT_BASE}/{self.plan}/v2/posts/",
            f"{DEFAULT_BASE}/v1/posts/",
        ]
        params = {
            "auth_token": self.token,
            "public": "true",
            "currencies": self._currencies_param(),
            "kind": "news",
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            last_err: Exception | None = None
            data: dict[str, Any] | None = None
            for url in urls:
                try:
                    resp = await client.get(url, params=params)
                    if resp.status_code >= 400:
                        last_err = httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}",
                            request=resp.request,
                            response=resp,
                        )
                        continue
                    data = resp.json()
                    break
                except Exception as exc:
                    last_err = exc
                    continue
            if data is None:
                raise last_err or RuntimeError("CryptoPanic unreachable")

        results = data.get("results") or data.get("posts") or []
        out: list[NewsEvent] = []
        for row in results[:limit]:
            ev = self._parse_row(row)
            if ev is not None:
                out.append(ev)
        return out

    def _parse_row(self, row: dict[str, Any]) -> NewsEvent | None:
        title = (row.get("title") or "").strip()
        if not title:
            return None
        eid = str(row.get("id") or row.get("slug") or abs(hash(title)) % 10_000_000)
        published = _parse_ts(row.get("published_at") or row.get("created_at"))
        currencies = row.get("currencies") or []
        assets: list[str] = []
        for c in currencies:
            if isinstance(c, dict):
                code = c.get("code") or c.get("title")
                if code:
                    assets.append(str(code).upper())
            elif c:
                assets.append(str(c).upper())
        votes = row.get("votes") or {}
        important = bool(row.get("important")) or int(votes.get("important") or 0) > 0
        # Pre-score hint; intel pipeline recomputes formally
        score_hint = 0.55
        if important:
            score_hint = 0.75
        panic = row.get("panic_score") or row.get("panicScore")
        if panic is not None:
            try:
                score_hint = max(score_hint, min(1.0, float(panic) / 100.0))
            except (TypeError, ValueError):
                pass

        domain = ""
        src = row.get("source")
        if isinstance(src, dict):
            domain = str(src.get("domain") or src.get("title") or "")
        elif src:
            domain = str(src)

        return NewsEvent(
            event_id=f"cp-{eid}",
            title=title,
            source=f"cryptopanic/{domain}" if domain else "cryptopanic",
            tier="WATCH",
            score=score_hint,
            assets=assets or ["BTC"],
            url=row.get("url") or row.get("original_url"),
            published_at=published,
            impact_note="Powered by CryptoPanic" if important else "CryptoPanic headline",
            event_type="other",
            raw={
                "provider": "cryptopanic",
                "mode": "live",
                "powered_by": "CryptoPanic",
                "votes": votes,
                "important": important,
            },
        )


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
