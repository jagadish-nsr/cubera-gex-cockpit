"""Pre-open overnight news digest — US / Asia / India cues before cash open.

Composed through the morning briefing window and sealed near cash pre-open so the
desk has one overnight brief. Heuristic only. Fail-soft. Linked headlines for
further reading live in the Real-Time News & Catalysts stream; this card is the
one-paragraph overnight summary (articles stay in Real-Time News).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[1]
JOURNAL_DIR = ROOT / "data" / "journal"

# Explicit keyword map — never use substring checks like ``"NIFTY" in "BANKNIFTY"``.
STOCK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "RELIANCE": ("reliance", "ril"),
    "HDFCBANK": ("hdfc bank", "hdfcbank"),
    "ICICIBANK": ("icici bank", "icicibank"),
    "SBIN": ("sbi ", "state bank", "sbin"),
    "INFY": ("infosys", "infy"),
    "TCS": ("tcs", "tata consultancy"),
    "WIPRO": ("wipro",),
    "HCLTECH": ("hcl tech", "hcltech"),
    "TATAMOTORS": ("tata motors", "tatamotors"),
    "M&M": ("m&m", "mahindra"),
    "MARUTI": ("maruti", "suzuki"),
    "BAJAJ-AUTO": ("bajaj auto", "bajaj-auto"),
    "TATASTEEL": ("tata steel", "tatasteel"),
    "JSWSTEEL": ("jsw steel", "jswsteel"),
    "HINDALCO": ("hindalco",),
    "SUNPHARMA": ("sun pharma", "sunpharma"),
    "DRREDDY": ("dr reddy", "drreddy"),
    "CIPLA": ("cipla",),
    "HINDUNILVR": ("hindustan unilever", "hul", "hindunilvr"),
    "ITC": ("itc ", " itc"),
    "NESTLEIND": ("nestle",),
    "ONGC": ("ongc",),
    "BPCL": ("bpcl",),
    "NTPC": ("ntpc",),
    "POWERGRID": ("powergrid", "power grid"),
    "LT": ("larsen", "l&t", "larsen & toubro"),
    "AXISBANK": ("axis bank", "axisbank"),
    "KOTAKBANK": ("kotak", "kotakbank"),
    "BAJFINANCE": ("bajaj finance", "bajfinance"),
    "BHARTIARTL": ("bharti", "airtel"),
}

SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Bank": ("bank", "banks", "bfsi", "lending", "nib", "credit growth"),
    "Pvt Bank": ("private bank", "pvt bank", "hdfc bank", "icici", "axis bank", "kotak"),
    "Energy": ("energy", "power", "ntpc", "powergrid", "adani power"),
    "IT": ("it sector", "infosys", "tcs", "wipro", "hcl", "tech earnings", "software"),
    "Oil & Gas": ("oil", "crude", "brent", "opec", "ongc", "bpcl", "reliance", "petroleum", "gas"),
    "FMCG": ("fmcg", "hindustan unilever", "hul", "itc", "nestle", "consumer staple"),
    "Auto": ("auto", "automobile", "maruti", "tata motors", "mahindra", "bajaj auto", "ev "),
    "Healthcare": ("healthcare", "hospital", "apollo"),
    "Metal": ("metal", "steel", "aluminium", "copper", "tata steel", "jsw", "hindalco", "commodity"),
    "Pharma": ("pharma", "pharmaceutical", "sun pharma", "cipla", "dr reddy", "drug"),
    "Cons Durables": ("consumer durable", "durables", "appliance", "electronics retail"),
}

MACRO_SECTOR_HINTS: dict[str, tuple[str, ...]] = {
    "Geopolitics": ("Bank", "Energy", "Oil & Gas", "Metal"),
    "Energy & Crude": ("Oil & Gas", "Energy", "Auto"),
    "Macro & Policy": ("Bank", "Pvt Bank", "IT", "FMCG"),
}

# Section keys for the overnight brief (order matters for UI).
SECTION_SPECS: tuple[tuple[str, str], ...] = (
    ("us_global", "US & global cues"),
    ("asia", "Asia open"),
    ("geopolitics_trade", "Geopolitics & trade"),
    ("india", "India sectors & heavyweights"),
)

_US_GLOBAL_KWS = (
    "wall street", "dow", "nasdaq", "s&p", "us stock", "us market", "us equity",
    "us close", "us session", "us yield", "treasury", "fed ", "fomc", "dxy",
    "dollar index", "us future", "global cue", "global market", "overnight cue",
    "european market", "stoxx",
)
_ASIA_KWS = (
    "asia", "asian", "hang seng", "nikkei", "kospi", "tokyo", "hong kong",
    "shanghai", "asx", "sgx", "gift nifty", "gift ", "asia open",
)

_SEAL_LOCK = threading.Lock()


def tag_headline(title: str, category: str = "") -> tuple[list[str], list[str]]:
    """Return (related_sectors, related_stocks) for a headline. Pure heuristic."""
    t = f" {title.lower()} "
    stocks: list[str] = []
    for sym, kws in STOCK_KEYWORDS.items():
        if any(kw in t for kw in kws):
            stocks.append(sym)

    sectors: list[str] = []
    for sec, kws in SECTOR_KEYWORDS.items():
        if any(kw in t for kw in kws):
            sectors.append(sec)

    if not sectors and category in MACRO_SECTOR_HINTS:
        sectors = list(MACRO_SECTOR_HINTS[category])

    if not sectors and stocks:
        stock_to_sector = {
            "RELIANCE": "Oil & Gas", "ONGC": "Oil & Gas", "BPCL": "Oil & Gas",
            "HDFCBANK": "Pvt Bank", "ICICIBANK": "Pvt Bank", "AXISBANK": "Pvt Bank",
            "KOTAKBANK": "Pvt Bank", "SBIN": "Bank",
            "INFY": "IT", "TCS": "IT", "WIPRO": "IT", "HCLTECH": "IT",
            "TATAMOTORS": "Auto", "M&M": "Auto", "MARUTI": "Auto", "BAJAJ-AUTO": "Auto",
            "TATASTEEL": "Metal", "JSWSTEEL": "Metal", "HINDALCO": "Metal",
            "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
            "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
            "NTPC": "Energy", "POWERGRID": "Energy",
        }
        for s in stocks:
            sec = stock_to_sector.get(s)
            if sec and sec not in sectors:
                sectors.append(sec)

    return sectors, stocks


def classify_overnight_bucket(title: str, category: str = "") -> str:
    """Assign an overnight digest section. Pure."""
    t = title.lower()
    cat = (category or "").lower()
    if cat == "geopolitics" or any(k in t for k in (
        "war", "missile", "sanction", "ukraine", "russia", "iran", "israel",
        "taiwan", "gaza", "trade deal", "trade war", "tariff",
    )):
        return "geopolitics_trade"
    if any(k in t for k in _ASIA_KWS):
        return "asia"
    if cat == "global cues" or any(k in t for k in _US_GLOBAL_KWS):
        return "us_global"
    return "india"


def _empty_digest(*, reason: str, as_of_ist: str = "", window_label: str = "") -> dict:
    return {
        "status": "UNAVAILABLE",
        "sealed": False,
        "as_of_ist": as_of_ist,
        "window_label": window_label or reason,
        "catalyst_risk": "LOW",
        "one_liner": reason,
        "narrative": reason,
        "narrative_source": "heuristic",
        "llm_model": None,
        "top_events": [],
        "sections": [],
        "sector_drivers": [],
        "event_count": 0,
        "overnight_impact": 0,
    }


def _catalyst_risk(impact: int, top_title: str) -> str:
    shock_keywords = (
        "war", "missile", "airstrike", "nuclear", "circuit",
        "ceasefire", "rate cut", "rate hike", "trade war",
    )
    is_shock = any(k in top_title.lower() for k in shock_keywords) if top_title else False
    if impact >= 8 or is_shock:
        return "HIGH_SHOCK"
    if impact >= 4:
        return "MODERATE"
    return "LOW"


def _narrative(sections: list[dict], *, overnight_impact: int, catalyst_risk: str) -> str:
    """One readable overnight paragraph. Article links live in Real-Time News, not here."""
    chunks: list[str] = []
    for sec in sections:
        items = sec.get("items") or []
        if not items:
            continue
        lead = items[0]["title"].rstrip(".")
        if len(items) == 1:
            chunks.append(f"{sec['label']} — {lead}")
        else:
            extras = "; ".join(e["title"].rstrip(".") for e in items[1:3])
            chunks.append(f"{sec['label']} — {lead}; also {extras}")
    if not chunks:
        return (
            "Overnight tape is quiet or unavailable — no market-moving US, Asia, "
            "geopolitics/trade, or India-sector catalysts ranked yet. Check Real-Time "
            "News & Catalysts for the live linked tape."
        )
    para = ". ".join(chunks) + "."
    if overnight_impact >= 4:
        para += (
            f" Catalyst intensity {overnight_impact} "
            f"({catalyst_risk.replace('_', ' ').title()})."
        )
    para += " Full headlines with source links are in Real-Time News & Catalysts."
    return para


def _one_liner(sections: list[dict], *, overnight_impact: int, catalyst_risk: str) -> str:
    return _narrative(sections, overnight_impact=overnight_impact, catalyst_risk=catalyst_risk)


def _build_sections(events: list[dict]) -> list[dict]:
    by_key: dict[str, list[dict]] = {k: [] for k, _ in SECTION_SPECS}
    for ev in events:
        key = ev.get("bucket") or "india"
        if key not in by_key:
            key = "india"
        if len(by_key[key]) < 8:
            by_key[key].append(ev)
    out: list[dict] = []
    for key, label in SECTION_SPECS:
        items = by_key.get(key) or []
        if not items:
            continue
        out.append({"key": key, "label": label, "items": items})
    return out


def build_pre_open_digest(
    news_snap: dict | None,
    sector_snap: dict | None = None,
    *,
    now_ist: datetime | None = None,
    session_date: date | None = None,
    overnight_window_min: int | None = None,
    window_label: str | None = None,
) -> dict:
    """Build a sectional overnight digest (US / Asia / geo / India). Pure / fail-soft."""
    if now_ist is None:
        now_ist = datetime.now(IST)
    elif now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    else:
        now_ist = now_ist.astimezone(IST)

    as_of = now_ist.strftime("%H:%M IST")
    if overnight_window_min is None or window_label is None:
        try:
            from data.news import minutes_since_prior_cash_close
            mins, label = minutes_since_prior_cash_close(now_ist)
            overnight_window_min = overnight_window_min or mins
            window_label = window_label or label
        except Exception:  # noqa: BLE001
            overnight_window_min = overnight_window_min or (16 * 60)
            window_label = window_label or "since ~16h ago"

    if not news_snap or not isinstance(news_snap, dict):
        return _empty_digest(
            reason="Overnight news tape unavailable.",
            as_of_ist=as_of,
            window_label=window_label,
        )

    raw_hls = news_snap.get("headlines") or []
    if not isinstance(raw_hls, list) or not raw_hls:
        return _empty_digest(
            reason="Overnight news tape quiet — no dated headlines in window.",
            as_of_ist=as_of,
            window_label=window_label,
        )

    overnight: list[dict] = []
    for h in raw_hls:
        if not isinstance(h, dict):
            continue
        title = str(h.get("title") or "").strip()
        if not title:
            continue
        try:
            age = int(h.get("age_min", 10**9))
        except (TypeError, ValueError):
            age = 10**9
        if age <= int(overnight_window_min):
            overnight.append(h)

    if not overnight:
        overnight = [h for h in raw_hls if isinstance(h, dict) and str(h.get("title") or "").strip()]

    try:
        from data.news import is_market_mover
    except Exception:  # noqa: BLE001
        def is_market_mover(title: str) -> bool:  # type: ignore[misc]
            return True

    events: list[dict] = []
    seen_titles: set[str] = set()
    overnight_impact = 0
    for h in overnight:
        title = str(h.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        if not is_market_mover(title):
            continue
        seen_titles.add(title)
        try:
            impact = int(h.get("impact") or 0)
        except (TypeError, ValueError):
            impact = 0
        overnight_impact += impact
        cat = str(h.get("category") or "Markets & Economy")
        sectors, stocks = tag_headline(title, cat)
        if h.get("related_sectors"):
            sectors = list(h.get("related_sectors") or sectors)
        if h.get("related_stocks"):
            stocks = list(h.get("related_stocks") or stocks)
        bucket = classify_overnight_bucket(title, cat)
        events.append({
            "title": title,
            "url": str(h.get("url") or ""),
            "pub_time_ist": str(h.get("pub_time_ist") or ""),
            "date_ist": str(h.get("date_ist") or ""),
            "age_min": int(h.get("age_min") or 0),
            "impact": impact,
            "category": cat,
            "sentiment": str(h.get("sentiment") or "NEUTRAL"),
            "related_sectors": sectors,
            "related_stocks": stocks,
            "bucket": bucket,
        })

    events.sort(key=lambda e: (e["impact"], -e["age_min"]), reverse=True)
    # Cap overall overnight brief; sections further cap per bucket.
    events = events[:24]
    sections = _build_sections(events)

    # Light prior-session sector context (optional; not the main digest job)
    sector_rows: list[dict] = []
    if sector_snap and isinstance(sector_snap, dict):
        for r in (sector_snap.get("rows") or []):
            if not isinstance(r, dict) or str(r.get("group") or "sector") != "sector":
                continue
            label = str(r.get("label") or "")
            if not label:
                continue
            pct = r.get("pct")
            try:
                pct_f = float(pct) if pct is not None else None
            except (TypeError, ValueError):
                pct_f = None
            # Reject stub levels from poisoned caches (last==1 style rows still carry pct)
            sector_rows.append({"label": label, "pct": pct_f})

    sector_rows_sorted = sorted(
        [s for s in sector_rows if s["pct"] is not None],
        key=lambda s: abs(float(s["pct"])),
        reverse=True,
    )
    watch = sector_rows_sorted[:4] if sector_rows_sorted else []
    sector_drivers: list[dict] = []
    for s in watch:
        label = s["label"]
        matched = next(
            (ev for ev in events if label in (ev.get("related_sectors") or [])),
            None,
        )
        sector_drivers.append({
            "sector": label,
            "pct": s["pct"],
            "matched_headline": matched["title"] if matched else None,
            "matched_url": matched["url"] if matched else None,
            "note": (
                f"Matched: {matched['title'][:100]}"
                if matched
                else "No matched catalyst in overnight tape"
            ),
        })

    top_title = events[0]["title"] if events else ""
    risk = _catalyst_risk(overnight_impact, top_title)
    narrative = _narrative(sections, overnight_impact=overnight_impact, catalyst_risk=risk)
    narrative_source = "heuristic"
    llm_model = None
    try:
        from analytics.llm_client import complete_chat, llm_enabled, model_name
        from analytics.llm_prompts import digest_narrative_messages
        if llm_enabled("digest"):
            rewritten = complete_chat(
                digest_narrative_messages(
                    events, catalyst_risk=risk, overnight_impact=overnight_impact,
                ),
                purpose="digest_narrative",
                temperature=0.2,
                max_tokens=420,
            )
            if rewritten and len(rewritten.strip()) >= 40:
                narrative = rewritten.strip()
                narrative_source = "genai"
                llm_model = model_name()
    except Exception as ex:  # noqa: BLE001 - fail soft to heuristic
        log.warning("digest LLM rewrite skipped: %s", ex)

    sess = session_date or now_ist.date()
    return {
        "status": "OK",
        "sealed": False,
        "session_date": sess.isoformat(),
        "as_of_ist": as_of,
        "window_label": window_label,
        "catalyst_risk": risk,
        "narrative": narrative,
        "one_liner": narrative,
        "narrative_source": narrative_source,
        "llm_model": llm_model,
        "top_events": events,
        "sections": sections,
        "sector_drivers": sector_drivers,
        "event_count": len(events),
        "overnight_impact": overnight_impact,
    }


def digest_path(session_date: date) -> Path:
    return JOURNAL_DIR / f"pre_open_digest_{session_date.isoformat()}.json"


def load_sealed_digest(session_date: date) -> dict | None:
    path = digest_path(session_date)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") == "OK":
            data["sealed"] = True
            return data
    except Exception as ex:  # noqa: BLE001
        log.warning("Failed to read sealed pre-open digest %s: %s", path, ex)
    return None


def seal_digest(digest: dict, session_date: date | None = None) -> dict:
    """Persist first successful OK digest for the session date. Idempotent."""
    if not digest or digest.get("status") != "OK":
        return digest
    try:
        sess = session_date or date.fromisoformat(str(digest.get("session_date")))
    except Exception:
        sess = datetime.now(IST).date()

    path = digest_path(sess)
    with _SEAL_LOCK:
        existing = load_sealed_digest(sess)
        if existing is not None:
            return existing
        try:
            JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
            payload = dict(digest)
            payload["sealed"] = True
            payload["sealed_at_ist"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            payload["session_date"] = sess.isoformat()
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            return payload
        except Exception as ex:  # noqa: BLE001
            log.warning("Failed to seal pre-open digest: %s", ex)
            return digest


def resolve_pre_open_digest(
    news_snap: dict | None,
    sector_snap: dict | None = None,
    *,
    now_ist: datetime | None = None,
    session_phase: str | None = None,
    force_rebuild: bool = False,
) -> dict:
    """Rebuild through morning briefing; seal near cash pre-open; serve sealed after open.

    PRE_MARKET_SETUP (06:30–09:00): keep refreshing the overnight brief (no seal yet).
    PRE_MARKET_ENTRY / DISCOVERY (~09:00–09:15): compose and seal.
    After open: prefer the sealed file so midday RSS cannot rewrite it.
    """
    if now_ist is None:
        now_ist = datetime.now(IST)
    elif now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    else:
        now_ist = now_ist.astimezone(IST)

    sess = now_ist.date()
    phase = (session_phase or "").upper()
    seal_phases = {"PRE_MARKET_ENTRY", "PRE_MARKET_DISCOVERY"}
    briefing_phases = {"PRE_MARKET_SETUP", "PRE_MARKET_ENTRY", "PRE_MARKET_DISCOVERY", "OFF_HOURS", ""}
    post_open = bool(phase) and phase not in briefing_phases

    if not force_rebuild and post_open:
        sealed = load_sealed_digest(sess)
        if sealed is not None:
            return sealed

    if not force_rebuild and phase in seal_phases:
        sealed = load_sealed_digest(sess)
        if sealed is not None and sealed.get("event_count", 0) > 0:
            return sealed

    digest = build_pre_open_digest(news_snap, sector_snap, now_ist=now_ist, session_date=sess)
    if digest.get("status") == "OK" and digest.get("event_count", 0) > 0:
        if phase in seal_phases:
            return seal_digest(digest, sess)
        if post_open and load_sealed_digest(sess) is None:
            return seal_digest(digest, sess)
    return digest



def enrich_headlines_with_tags(headlines: list[dict] | None) -> list[dict]:
    """Attach related_sectors / related_stocks onto headline dicts (copy)."""
    out: list[dict] = []
    for h in headlines or []:
        if not isinstance(h, dict):
            continue
        item = dict(h)
        secs, stocks = tag_headline(
            str(item.get("title") or ""),
            str(item.get("category") or ""),
        )
        item.setdefault("related_sectors", secs)
        item.setdefault("related_stocks", stocks)
        out.append(item)
    return out

