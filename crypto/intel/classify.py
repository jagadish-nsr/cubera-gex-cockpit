"""Keyword classification rules (Gemma fallback path).

Extracts event_type, assets, venues from untrusted headline text.
Used when ``intel.gemma`` is off, or when Gemma times out / fails schema.
Advisory only — never creates a GO.
"""

from __future__ import annotations

import re
from typing import Iterable

# (event_type, keywords) — first match wins; order = priority
_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("hack", ("hack", "exploit", "breach", "stolen", "drain", "rug pull", "rugpull")),
    ("outage", ("outage", "halt", "halted", "downed", "service disruption")),
    ("maintenance", ("maintenance", "scheduled downtime", "system upgrade")),
    ("liquidation", ("liquidation", "liquidated", "cascade", "long squeeze", "short squeeze")),
    ("etf_flow", ("etf", "ibit", "spot etf", "inflow", "outflow", "blackrock")),
    ("regulation", ("sec", "cftc", "rbi", "fiu", "ban", "lawsuit", "regulator", "compliance")),
    ("macro", ("fomc", "cpi", "payroll", "nonfarm", "fed ", "interest rate", "inflation", "jobs report")),
    ("listing", ("lists", "listing", "listed", "will list", "new pair")),
    ("delisting", ("delist", "delisting", "remove pair")),
    ("expiry", ("options expiry", "expiry", "expiration", "weekly expiry")),
    ("unlock", ("token unlock", "unlock", "vesting cliff")),
    ("funding", ("funding rate", "funding flip", "negative funding", "positive funding")),
]

_ASSET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("BTC", re.compile(r"\b(btc|bitcoin)\b", re.I)),
    ("ETH", re.compile(r"\b(eth|ethereum)\b", re.I)),
    ("SOL", re.compile(r"\b(sol|solana)\b", re.I)),
]

_VENUE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("deribit", re.compile(r"\bderibit\b", re.I)),
    ("delta", re.compile(r"\bdelta(\s+exchange)?\b", re.I)),
    ("binance", re.compile(r"\bbinance\b", re.I)),
    ("bybit", re.compile(r"\bbybit\b", re.I)),
    ("coinbase", re.compile(r"\bcoinbase\b", re.I)),
]

# Prior-move proxy by event type (heuristic, not calibrated)
PRIOR_MOVE_BY_TYPE: dict[str, float] = {
    "hack": 0.85,
    "liquidation": 0.80,
    "macro": 0.75,
    "regulation": 0.70,
    "outage": 0.65,
    "maintenance": 0.55,
    "etf_flow": 0.55,
    "expiry": 0.50,
    "unlock": 0.50,
    "listing": 0.40,
    "delisting": 0.45,
    "funding": 0.35,
    "other": 0.30,
}

SOURCE_RELIABILITY: dict[str, float] = {
    "cryptopanic": 0.55,
    "fixture-calendar": 0.70,
    "fixture-tape": 0.70,
    "fixture-digest": 0.45,
    "calendar-stub": 0.75,
    "delta-status-stub": 0.80,
    "mock": 0.50,
}


def classify_headline(
    title: str,
    *,
    assets_hint: Iterable[str] | None = None,
    venues_hint: Iterable[str] | None = None,
    event_type_hint: str | None = None,
) -> dict[str, object]:
    """Return event_type, assets, venues from keywords (+ optional hints)."""
    text = title or ""
    event_type = event_type_hint if event_type_hint and event_type_hint != "other" else None
    if event_type is None:
        lower = text.lower()
        for etype, keys in _TYPE_RULES:
            if any(k in lower for k in keys):
                event_type = etype
                break
        else:
            event_type = "other"

    assets = _uniq([*(assets_hint or []), *[a for a, pat in _ASSET_PATTERNS if pat.search(text)]])
    venues = _uniq([*(venues_hint or []), *[v for v, pat in _VENUE_PATTERNS if pat.search(text)]])
    return {
        "event_type": event_type,
        "assets": assets,
        "venues": venues,
        "prior_move_proxy": float(PRIOR_MOVE_BY_TYPE.get(str(event_type), 0.3)),
    }


def reliability_for_source(source: str) -> float:
    src = (source or "").lower()
    for key, val in SOURCE_RELIABILITY.items():
        if key in src:
            return val
    if "cryptopanic" in src:
        return 0.55
    return 0.45


def novelty_against(title: str, seen_titles: list[str]) -> float:
    """Crude near-duplicate novelty: 1.0 fresh, ~0.3 if token-overlap high."""
    tokens = _tokens(title)
    if not tokens or not seen_titles:
        return 1.0
    best = 0.0
    for prev in seen_titles:
        other = _tokens(prev)
        if not other:
            continue
        overlap = len(tokens & other) / max(len(tokens | other), 1)
        best = max(best, overlap)
    if best >= 0.75:
        return 0.25
    if best >= 0.5:
        return 0.55
    return 1.0


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2}


def _uniq(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in items:
        u = str(x).upper() if len(str(x)) <= 4 else str(x).lower()
        # assets upper, venues lower
        if len(str(x)) <= 4 and str(x).isalpha():
            key = str(x).upper()
            val = str(x).upper()
        else:
            key = str(x).lower()
            val = str(x).lower()
        if key not in seen:
            seen.add(key)
            out.append(val)
    return out
