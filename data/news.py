"""Intraday news-intensity signal (Phase B confound control).

Captured every snapshot alongside the GEX levels so we can IDENTIFY and quarantine
*news-dominated* trading days — days whose direction was set by an exogenous catalyst
(e.g. war headlines), not by dealer hedging. On those days the dealer-sign test is
meaningless (CLAUDE.md: "GEX is a probability map, not a predictor. Headlines
override"), so they must be flagged out of any sign-validation read.

This is a deliberately WEAK, transparent heuristic, NOT a verdict: a headline burst
is not proof the market moved on it, and silence is not proof of calm. It errs toward
flagging, is paired with a manual override (``data/journal/event_days.txt``), and the
analyser always reports sign-stats BOTH with and without flagged days.

Signal (stateless, per snapshot):
  - count : market / macro / geopolitics headlines published in the last WINDOW_MIN min
  - impact: keyword-weighted score over those fresh headlines (war/RBI/Fed/crude/...)
  - top   : the single highest-impact recent headline (kept for the audit trail)

Free RSS, no API key, stdlib XML parsing. Fail-soft like ``data/nse.fetch_india_vix``:
one feed failing drops just that feed; total failure -> None, never breaks a GEX
snapshot.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

WINDOW_MIN = 25  # "fresh" = published within this many minutes of the snapshot

# Free RSS, no key. One India-markets, one macro/economy, one top-stories
# (geopolitics) feed — the war-type catalysts that actually moved Nifty live in the
# last one. Verified-live to parse with stdlib ET and carry a pubDate.
FEED_URLS = (
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
    "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
)

# Weighted impact keywords (lower-cased substring match). Higher = more likely to be
# a market-moving catalyst. This is a tunable heuristic, not a model — edit freely.
IMPACT_KEYWORDS = {
    # geopolitics / shocks (the big movers)
    "war": 3, "missile": 3, "airstrike": 3, "strike": 2, "attack": 3, "ceasefire": 3,
    "invasion": 3, "nuclear": 3, "border": 2, "military": 2, "terror": 3, "conflict": 2,
    # macro / policy
    "rbi": 2, "fed": 2, "fomc": 2, "rate cut": 2, "rate hike": 2, "inflation": 2,
    "cpi": 2, "gdp": 1, "tariff": 2, "sanction": 2, "crude": 2, "oil price": 2,
    "budget": 2, "rupee": 1, "downgrade": 2, "default": 2,
    # market structure
    "crash": 3, "plunge": 2, "selloff": 2, "sell-off": 2, "circuit": 2, "tumble": 2,
    "surge": 1, "rally": 1,
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _parse_pubdate(s: str | None) -> datetime | None:
    """RFC-822 ('Sat, 13 Jun 2026 16:14:29 +0530') or ISO-8601 -> aware UTC datetime,
    or None if absent/unparseable. Feeds mix the two formats, so try both."""
    if not s:
        return None
    s = s.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed(xml_text) -> list[tuple[datetime | None, str]]:
    """Parse RSS 2.0 XML -> [(pubDate_utc|None, title), ...]. Pure. A malformed feed
    raises ET.ParseError, which fetch_news catches to drop that one feed."""
    root = ET.fromstring(xml_text)
    out: list[tuple[datetime | None, str]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        out.append((_parse_pubdate(item.findtext("pubDate")), title))
    return out


def _impact(title: str) -> int:
    t = title.lower()
    return sum(w for kw, w in IMPACT_KEYWORDS.items() if kw in t)


def score_news(items, now_utc: datetime, window_min: int = WINDOW_MIN) -> dict:
    """Reduce (pubDate, title) items to {count, impact, top}. Counts ONLY items whose
    pubDate is within the last ``window_min`` minutes (a *burst* of fresh news); an
    item with no pubDate can't be placed in time, so it's ignored for the count.
    ``impact`` sums the keyword weight of those fresh items; ``top`` is the
    highest-impact fresh headline. Stateless: depends only on (items, now)."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    cutoff = now_utc - timedelta(minutes=window_min)
    horizon = now_utc + timedelta(minutes=2)   # small slack for publisher clock skew
    fresh = [(pd, t) for (pd, t) in items if pd is not None and cutoff <= pd <= horizon]
    impact = sum(_impact(t) for _, t in fresh)
    top = max(fresh, key=lambda it: (_impact(it[1]), it[0]))[1] if fresh else ""
    return {"count": len(fresh), "impact": impact, "top": top[:120]}


def fetch_news(session=None, timeout: int = 10, now_utc: datetime | None = None,
               feed_urls=FEED_URLS) -> dict | None:
    """Best-effort news-intensity for *now*: {count, impact, top} or None.

    Fail-soft (mirrors data/nse.fetch_india_vix): a single feed erroring is dropped
    and the rest still count; only if EVERY feed fails do we return None, so a news
    miss never breaks a GEX snapshot. ``top`` is the highest-impact fresh headline.
    """
    import requests

    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        s = session or requests.Session()
        if session is None:
            s.headers.update(_HEADERS)
        items: list[tuple[datetime | None, str]] = []
        ok = False
        for url in feed_urls:
            try:
                r = s.get(url, timeout=timeout)
                r.raise_for_status()
                items.extend(parse_feed(r.content))
                ok = True
            except Exception:  # noqa: BLE001 - drop just this feed, keep the others
                continue
        if not ok:
            return None
        return score_news(items, now_utc)
    except Exception:  # noqa: BLE001 - fail soft; news is optional context
        return None
