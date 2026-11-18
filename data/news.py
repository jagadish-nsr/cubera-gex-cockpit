"""Intraday news-intensity signal (Phase B confound control) + overnight headline tape.

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
  - headlines: structured tape with source URLs for Market Intelligence / pre-open digest

Free RSS, no API key, stdlib XML parsing. Fail-soft like ``data/nse.fetch_india_vix``:
one feed failing drops just that feed; total failure -> None, never breaks a GEX
snapshot.
"""

from __future__ import annotations

import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

WINDOW_MIN = 25  # Phase B "fresh" = published within this many minutes of the snapshot
# Overnight / pre-open digest horizon fallback when prior-close calendar is unavailable.
OVERNIGHT_FALLBACK_HOURS = 16
# How far back the structured headline list reaches (covers overnight catalysts).
HEADLINE_HORIZON_MIN = OVERNIGHT_FALLBACK_HOURS * 60
HEADLINE_LIMIT_INTRADAY = 15
HEADLINE_LIMIT_DIGEST = 25

IST = timezone(timedelta(hours=5, minutes=30))

# Free RSS, no key. Markets / economy / stocks only — no general top-stories feed
# (TOI top stories flooded the digest with non-market noise). Moneycontrol 403 — omit.
FEED_URLS = (
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://www.livemint.com/rss/markets",
)

# Weighted impact keywords. Avoid bare "crash"/"strike" — traffic/sports false positives.
IMPACT_KEYWORDS = {
    "war": 3, "missile": 3, "airstrike": 3, "missile strike": 3,
    "ceasefire": 3, "invasion": 3, "nuclear": 3, "military": 2, "terror": 3,
    "geopolitical": 2, "sanction": 2, "conflict": 2,
    "trade deal": 3, "tariff": 2, "trade war": 3, "export ban": 2, "import duty": 2,
    "rbi": 2, "fed": 2, "fomc": 2, "rate cut": 2, "rate hike": 2, "inflation": 2,
    "cpi": 2, "gdp": 1, "crude": 2, "oil price": 2, "brent": 2, "opec": 2,
    "budget": 2, "rupee": 1, "downgrade": 2, "default": 2, "repo": 2,
    "market crash": 3, "stock crash": 3, "selloff": 2, "sell-off": 2,
    "circuit": 2, "market plunge": 2, "stocks plunge": 2, "nifty": 1, "sensex": 1,
    "wall street": 1, "sgx": 1, "gift nifty": 2,
}

_MOVER_GLOBAL_CUES = (
    "wall street", "dow ", "dow,", "nasdaq", "s&p", "us stocks", "us equity",
    "us yields", "treasury yield", "bond yield", "dollar index", "dxy",
    "asia markets", "asian markets", "hang seng", "nikkei", "kospi",
    "european markets", "stoxx", "sgx", "gift nifty", "global cues", "global markets",
    "us futures", "oil futures", "gold price", "brent", "wti", "crude oil", "opec",
)
_MOVER_GEOPOLITICS = (
    "war", "missile", "airstrike", "ceasefire", "invasion", "nuclear", "sanction",
    "geopolitical", "ukraine", "russia", "iran", "israel", "gaza", "taiwan",
    "south china sea", "border tension", "military conflict", "nato",
)
_MOVER_TRADE = (
    "trade deal", "trade war", "tariff", "wto", "export ban", "import duty",
    "customs duty", "bilateral trade", "free trade", "fta ",
)
_MOVER_MACRO = (
    "rbi", "reserve bank", "federal reserve", "fed ", "fomc", "rate cut", "rate hike",
    "inflation", "cpi", "gdp", "repo rate", "mpc ", "budget", "fiscal deficit",
    "rupee", "fii ", "dii ", "foreign fund", "sebi",
)
_MOVER_INDIA_MARKETS = (
    "nifty", "sensex", "bank nifty", "banknifty", "india market", "indian market",
    "indian stocks", "equity market", "stock market", "dalal street",
    "f&o", "derivatives",
)
_MOVER_INDIA_SECTORS = (
    "banking stock", "bank stocks", "pvt bank", "private bank", "it stock", "it sector",
    "pharma stock", "pharma sector", "auto stock", "auto sector", "metal stock",
    "metal sector", "fmcg", "oil & gas", "oil and gas", "psu bank",
    "indian bank", "indian it", "indian pharma", "indian auto", "banks rally",
    "banks lead", "bank rally",
)
_MOVER_HEAVYWEIGHTS = (
    "reliance", "hdfc bank", "hdfcbank", "icici bank", "icicibank", "infosys",
    "tata consultancy", "wipro", "hcl tech", "hcltech", "tata motors", "mahindra",
    "maruti", "bajaj auto", "tata steel", "jsw steel", "hindalco", "sun pharma",
    "dr reddy", "cipla", "hindustan unilever", "nestle", "ongc", "bpcl", "ntpc",
    "powergrid", "larsen", "l&t", "axis bank", "kotak", "bajaj finance", "bharti",
    "airtel", "state bank", "adani", "ltimindtree", "tech mahindra",
)
_MOVER_BUCKETS = (
    _MOVER_GLOBAL_CUES + _MOVER_GEOPOLITICS + _MOVER_TRADE + _MOVER_MACRO
    + _MOVER_INDIA_MARKETS + _MOVER_INDIA_SECTORS + _MOVER_HEAVYWEIGHTS
)
_NOISE_PATTERNS = (
    "motorcycle", "motorcyclist", "court upholds", "court awards", "waste spill",
    "celebrity", "bollywood", "cricket score", "ipl ", "weather", "rainfall",
    "horoscope", "recipe", "lifestyle",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}




def _norm_words(title: str) -> str:
    return " " + re.sub(r"[^a-z0-9&]+", " ", title.lower()) + " "


def _kw_in(norm: str, kw: str) -> bool:
    k = kw.lower().strip()
    if not k:
        return False
    if " " in k:
        return f" {k} " in norm or k in norm
    return f" {k} " in norm


def is_market_mover(title: str) -> bool:
    """True only for global cues, geopolitics, trade/macro, India sectors, Nifty heavyweights."""
    if not title or not title.strip():
        return False
    raw = title.lower()
    if any(n in raw for n in _NOISE_PATTERNS):
        return False
    norm = _norm_words(title)
    if any(_kw_in(norm, kw) for kw in _MOVER_BUCKETS):
        return True
    if _kw_in(norm, "tcs") or _kw_in(norm, "sbi") or _kw_in(norm, "itc") or _kw_in(norm, "fed"):
        return True
    return False


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


def _item_url(item: ET.Element) -> str:
    """Prefer <link>; fall back to http(s) <guid>. Empty when neither is usable."""
    link = (item.findtext("link") or "").strip()
    if link.startswith("http://") or link.startswith("https://"):
        return link
    guid = (item.findtext("guid") or "").strip()
    if guid.startswith("http://") or guid.startswith("https://"):
        return guid
    return ""


def parse_feed(xml_text) -> list[tuple[datetime | None, str, str]]:
    """Parse RSS 2.0 XML -> [(pubDate_utc|None, title, url), ...]. Pure.

    A malformed feed raises ET.ParseError, which fetch_news catches to drop that one feed.
    ``url`` may be empty when the item has neither a usable link nor guid.
    """
    root = ET.fromstring(xml_text)
    out: list[tuple[datetime | None, str, str]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        out.append((_parse_pubdate(item.findtext("pubDate")), title, _item_url(item)))
    return out


def _impact(title: str) -> int:
    """Keyword impact for market movers only. Noise titles score 0."""
    if not is_market_mover(title):
        return 0
    norm = _norm_words(title)
    return sum(w for kw, w in IMPACT_KEYWORDS.items() if _kw_in(norm, kw) or kw in title.lower())


def classify_headline(title: str) -> tuple[str, str]:
    """Classifies a headline into (category, sentiment). Pure heuristic."""
    t = title.lower()
    norm = _norm_words(title)

    if any(_kw_in(norm, k) or k in t for k in (
        "war", "missile", "airstrike", "missile strike", "ceasefire", "invasion",
        "nuclear", "military", "terror", "geopolitical", "sanction", "iran", "israel",
        "russia", "ukraine", "taiwan", "gaza", "border tension",
    )):
        cat = "Geopolitics"
    elif any(k in t for k in ("crude", "brent", "wti", "opec", "oil price", "petroleum")):
        cat = "Energy & Crude"
    elif any(k in t for k in ("rbi", "fomc", "rate cut", "rate hike", "inflation", "cpi", "gdp", "tariff", "budget", "rupee", "repo", "central bank", "deficit", "trade deal", "trade war")) or _kw_in(norm, "fed"):
        cat = "Macro & Policy"
    elif any(k in t for k in ("results", "profit", "q1", "q2", "q3", "q4", "revenue", "earnings", "acquisition", "merger", "dividend", "buyback", "ipo", "stake")):
        cat = "Corporate & Earnings"
    else:
        cat = "Markets & Economy"

    bullish_kws = ("surge", "rally", "jump", "gain", "gains", "climb", "rise", "rises", "soar", "soars", "ease", "eases", "relief", "upbeat", "boost", "rebound", "positive", "advance", "advances")
    bearish_kws = ("plunge", "selloff", "sell-off", "slump", "downgrade", "rout", "drag", "bleeding", "market crash", "stock crash", "fear", "missile", "tension", "sanction", "tariff")
    # token-aware war
    bull_score = sum(1 for k in bullish_kws if k in t)
    bear_score = sum(1 for k in bearish_kws if k in t) + (1 if _kw_in(norm, "war") else 0)
    if bull_score > bear_score:
        sent = "BULLISH"
    elif bear_score > bull_score:
        sent = "BEARISH"
    else:
        sent = "NEUTRAL"
    return cat, sent


def _normalize_item(it) -> tuple[datetime | None, str, str]:
    """Accept (pub, title) or (pub, title, url) tuples from fixtures / mixed callers."""
    if not isinstance(it, (tuple, list)) or len(it) < 2:
        return None, "", ""
    pub, title = it[0], str(it[1] or "")
    url = str(it[2]) if len(it) >= 3 else ""
    return pub, title, url


def minutes_since_prior_cash_close(now_ist: datetime | None = None) -> tuple[int, str]:
    """Minutes from prior session cash close (15:30 IST) to now, plus a window label.

    Uses ``last_completed_session_date`` when available; falls back to
    ``OVERNIGHT_FALLBACK_HOURS`` if the calendar import fails.
    """
    if now_ist is None:
        now_ist = datetime.now(IST)
    elif now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    else:
        now_ist = now_ist.astimezone(IST)

    try:
        from data.market_snapshot import last_completed_session_date
        from data.session_calendar import clock_for

        sess = last_completed_session_date(now_ist)
        clock = clock_for(sess)
        close_min = int(getattr(clock, "cash_close_min", 15 * 60 + 30) or (15 * 60 + 30))
        close_dt = datetime(
            sess.year, sess.month, sess.day,
            close_min // 60, close_min % 60,
            tzinfo=IST,
        )
        mins = max(1, int((now_ist - close_dt).total_seconds() / 60))
        label = f"since prior close {close_min // 60:02d}:{close_min % 60:02d} IST ({sess.isoformat()})"
        return mins, label
    except Exception:  # noqa: BLE001 - calendar optional for pure tests
        mins = OVERNIGHT_FALLBACK_HOURS * 60
        return mins, f"since ~{OVERNIGHT_FALLBACK_HOURS}h ago (calendar unavailable)"


def score_news(
    items,
    now_utc: datetime,
    window_min: int = WINDOW_MIN,
    *,
    headline_limit: int = HEADLINE_LIMIT_INTRADAY,
    headline_horizon_min: int | None = HEADLINE_HORIZON_MIN,
) -> dict:
    """Reduce items to {count, impact, top, headlines}.

    Counts ONLY items whose pubDate is within the last ``window_min`` minutes (a
    *burst* of fresh news); an item with no pubDate can't be placed in time, so it's
    ignored for the count. ``impact`` sums the keyword weight of those fresh items;
    ``top`` is the highest-impact fresh headline. Headlines may reach farther back
    via ``headline_horizon_min`` (None = all dated items). Stateless.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    norm = [_normalize_item(it) for it in items]
    cutoff = now_utc - timedelta(minutes=window_min)
    horizon = now_utc + timedelta(minutes=2)   # small slack for publisher clock skew
    fresh = [
        (pd, t, u) for (pd, t, u) in norm
        if pd is not None and cutoff <= pd <= horizon and is_market_mover(t)
    ]
    impact = sum(_impact(t) for _, t, _ in fresh)
    top = max(fresh, key=lambda it: (_impact(it[1]), it[0]))[1] if fresh else ""

    dated_items = [it for it in norm if it[0] is not None]
    if headline_horizon_min is not None:
        h_cut = now_utc - timedelta(minutes=int(headline_horizon_min))
        dated_items = [it for it in dated_items if it[0] >= h_cut]

    def _sort_key(it):
        pd, t, _u = it
        is_f = 1 if (cutoff <= pd <= horizon) else 0
        return (is_f, _impact(t), pd.timestamp() if pd else 0.0)

    sorted_items = sorted(dated_items, key=_sort_key, reverse=True)
    headlines: list[dict] = []
    seen = set()
    for pd, t, url in sorted_items:
        t_clean = t.strip()
        if not t_clean or t_clean in seen:
            continue
        if not is_market_mover(t_clean):
            continue
        seen.add(t_clean)
        cat, sent = classify_headline(t_clean)
        age_m = max(0, int((now_utc - pd).total_seconds() / 60))
        ist_dt = pd.astimezone(IST)
        headlines.append({
            "title": t_clean,
            "url": url or "",
            "pub_time_ist": ist_dt.strftime("%H:%M IST"),
            "date_ist": ist_dt.strftime("%d-%b"),
            "age_min": age_m,
            "impact": _impact(t_clean),
            "category": cat,
            "sentiment": sent,
            "is_fresh": (cutoff <= pd <= horizon),
        })
        if len(headlines) >= headline_limit:
            break

    return {
        "count": len(fresh),
        "impact": impact,
        "top": top[:120],
        "headlines": headlines,
    }


_NEWS_CACHE: dict = {"at": 0.0, "data": None}
_NEWS_LOCK = threading.Lock()


def fetch_news(session=None, timeout: int = 10, now_utc: datetime | None = None,
               feed_urls=FEED_URLS, force: bool = False,
               *, headline_limit: int | None = None,
               window_min: int = WINDOW_MIN) -> dict | None:
    """Best-effort news-intensity for *now*: {count, impact, top, headlines} or None.

    Fail-soft (mirrors data/nse.fetch_india_vix): a single feed erroring is dropped
    and the rest still count; only if EVERY feed fails do we return None, so a news
    miss never breaks a GEX snapshot. ``top`` is the highest-impact fresh headline.
    Cached in-memory with a 5-minute TTL to eliminate repetitive network latency.

    Default ``headline_limit`` is the digest cap so Market Intelligence / pre-open
    digest share one fetch. Phase B burst metrics still use ``window_min`` (25).
    """
    import time
    global _NEWS_CACHE

    now_t = time.time()
    if not force and (now_t - _NEWS_CACHE["at"] < 300.0) and _NEWS_CACHE["data"] is not None:
        return _NEWS_CACHE["data"]

    import requests

    now_utc = now_utc or datetime.now(timezone.utc)
    limit = HEADLINE_LIMIT_DIGEST if headline_limit is None else int(headline_limit)
    try:
        s = session or requests.Session()
        if session is None:
            s.headers.update(_HEADERS)
        items: list[tuple[datetime | None, str, str]] = []
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
            try:
                from app.obs import metrics as obs_metrics
                obs_metrics.note_egress("news_miss")
            except Exception:
                pass
            return None
        res = score_news(
            items,
            now_utc,
            window_min=window_min,
            headline_limit=limit,
            headline_horizon_min=HEADLINE_HORIZON_MIN,
        )
        with _NEWS_LOCK:
            _NEWS_CACHE["at"] = now_t
            _NEWS_CACHE["data"] = res
        return res
    except Exception:  # noqa: BLE001 - fail soft; news is optional context
        try:
            from app.obs import metrics as obs_metrics
            obs_metrics.note_egress("news_miss")
        except Exception:
            pass
        return None
