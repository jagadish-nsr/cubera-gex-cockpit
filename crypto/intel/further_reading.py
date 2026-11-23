"""Curated further-reading links for CryptoCubera Market Intelligence.

Crypto desk only (BTC / ETH / SOL + Deribit / Delta / CryptoPanic).
Never links to NSE equity index docs (Nifty / Bank Nifty / Sensex).
"""

from __future__ import annotations

from typing import Any

from crypto.config import load_config

# Hard-coded defaults so the UI always has something useful offline.
_DEFAULT_LINKS: list[dict[str, str]] = [
    {
        "title": "Deribit market data — ticker & greeks",
        "url": "https://docs.deribit.com/api-reference/market-data/public-ticker",
        "note": "Option chain greeks, IV, open interest (signal venue).",
    },
    {
        "title": "Deribit options data collection",
        "url": "https://docs.deribit.com/articles/options-data-collection-best-practices",
        "note": "Best practices for streaming ticker / mark IV / chain.",
    },
    {
        "title": "Delta Exchange India API",
        "url": "https://docs.delta.exchange/",
        "note": "INR execution venue docs (orders never auto-placed here).",
    },
    {
        "title": "CryptoPanic API",
        "url": "https://cryptopanic.com/developers/api/",
        "note": "Headline ingest used by the intelligence feed.",
    },
    {
        "title": "Glassnode — options / GEX concepts",
        "url": "https://studio.glassnode.com/",
        "note": "Cross-check dealer-flow gamma ideas; not the live feed.",
    },
]


def further_reading_links() -> list[dict[str, str]]:
    """Return curated links from config, falling back to defaults."""
    cfg = load_config()
    intel = cfg.get("intel") or {}
    raw = intel.get("further_reading")
    if not isinstance(raw, list) or not raw:
        return [dict(x) for x in _DEFAULT_LINKS]
    out: list[dict[str, str]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        if not title or not url:
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        out.append(
            {
                "title": title,
                "url": url,
                "note": str(row.get("note") or "").strip(),
            }
        )
    return out or [dict(x) for x in _DEFAULT_LINKS]


def intel_meta_block(*, underlying: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fields merged into snapshot ``intel`` for the cockpit."""
    block: dict[str, Any] = {
        "further_reading": further_reading_links(),
        "desk": "CryptoCubera",
        "scope": "crypto",
        "underlying": (underlying or "").strip().upper(),
    }
    if extra:
        block.update(extra)
    return block
