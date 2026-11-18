"""Nifty-50 heavyweight stock tape — separate from the sector-index board.

Curated top names by approximate Nifty weight, with live LTP / % via Dhan
``NSE_EQ`` ``/marketfeed/quote``. Weights are hand-read from NSE factsheets
(same honesty rule as ``data/sectors.py``): rendered with ``~`` and a
``weights_as_of`` stamp. Never sum weights into a contribution maths.

Sector board (``data/sectors.py``) is untouched.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / ".cache" / "last_heavyweight_snap.json"

# ⚠ WEIGHTS ARE AN APPROXIMATION (NSE factsheet), not a live feed. VERIFY periodically.
WEIGHTS_AS_OF = "2025-10"

# (NSE trading symbol, short label, Dhan NSE_EQ security id, ~Nifty 50 weight %)
# Security ids resolved from Dhan scrip master (EQUITY / NSE / EQ) on 2026-11-18.
HEAVYWEIGHTS: list[tuple[str, str, int, float]] = [
    ("HDFCBANK", "HDFC Bank", 1333, 12.8),
    ("RELIANCE", "Reliance", 2885, 8.9),
    ("ICICIBANK", "ICICI Bank", 4963, 8.5),
    ("INFY", "Infosys", 1594, 6.0),
    ("ITC", "ITC", 1660, 4.2),
    ("TCS", "TCS", 11536, 3.9),
    ("BHARTIARTL", "Bharti Airtel", 10604, 3.8),
    ("LT", "L&T", 11483, 3.7),
    ("SBIN", "SBI", 3045, 3.0),
    ("AXISBANK", "Axis Bank", 5900, 2.9),
    ("KOTAKBANK", "Kotak Bank", 1922, 2.7),
    ("BAJFINANCE", "Bajaj Finance", 317, 2.4),
    ("HINDUNILVR", "HUL", 1394, 2.2),
    ("M&M", "M&M", 2031, 2.1),
    ("SUNPHARMA", "Sun Pharma", 3351, 1.6),
]

EQ_SEG = "NSE_EQ"

_TTL_S = 900.0
_FAIL_BACKOFF_S = 120.0
_cache: dict = {"at": 0.0, "rows": None, "src": None}
_refreshing = False


def heavyweight_symbols() -> list[str]:
    return [sym for sym, _label, _sid, _wt in HEAVYWEIGHTS]


def _row(symbol: str, label: str, pct, last, weight: float) -> dict:
    return {
        "symbol": symbol,
        "label": label,
        "pct": pct,
        "last": last,
        "weight": weight,
        "group": "heavyweight",
    }


def valid_heavyweight_rows(rows) -> bool:
    """Reject stub / poisoned cache rows."""
    if not isinstance(rows, list) or len(rows) < 5:
        return False
    known = {sym for sym, _l, _i, _w in HEAVYWEIGHTS}
    got = {str(r.get("symbol") or "") for r in rows if isinstance(r, dict)}
    # Reject obvious pytest stubs (₹1 last on a single fake ticker).
    for r in rows:
        if not isinstance(r, dict):
            return False
        last = r.get("last")
        if last is not None:
            try:
                if float(last) <= 1.0 and str(r.get("symbol") or "") not in known:
                    return False
            except (TypeError, ValueError):
                return False
    return len(got & known) >= 5


def fetch_heavyweights_dhan(timeout: int = 20) -> list[dict] | None:
    """One Dhan ``/marketfeed/quote`` for all curated NSE equity ids."""
    try:
        from data.dhan_live import _num, _post
        ids = [sid for _sym, _lab, sid, _wt in HEAVYWEIGHTS]
        r = _post("/marketfeed/quote", {EQ_SEG: ids}, timeout)
        if not r.ok:
            log.warning("heavyweights dhan quote HTTP %s", getattr(r, "status_code", "?"))
            return None
        cells = (r.json() or {}).get("data", {}).get(EQ_SEG, {})
        out: list[dict] = []
        for sym, label, sid, wt in HEAVYWEIGHTS:
            c = cells.get(str(sid))
            if not c:
                continue
            last = _num(c.get("last_price") or c.get("ltp"))
            chg = _num(c.get("net_change")) or None
            if not chg:
                prev = _num((c.get("ohlc") or {}).get("close"))
                chg = (last - prev) if (last and prev and abs(last - prev) > 1e-9) else None
            base = (last - chg) if (last is not None and chg is not None) else None
            pct = (chg / base * 100.0) if (base not in (None, 0.0) and chg is not None) else None
            if last is None and pct is None:
                continue
            out.append(_row(sym, label, pct, last, wt))
        return out or None
    except Exception as ex:  # noqa: BLE001
        log.warning("heavyweights dhan fetch failed: %s", ex)
        return None


def _pull() -> bool:
    global _refreshing
    try:
        rows = fetch_heavyweights_dhan()
        if not rows:
            _cache["at"] = time.time() - (_TTL_S - _FAIL_BACKOFF_S)
            return False
        rows.sort(key=lambda r: (r["pct"] is None, -(r["pct"] or 0.0)))
        _cache.update(at=time.time(), rows=rows, src="dhan")
        if valid_heavyweight_rows(rows):
            try:
                _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp = _CACHE_PATH.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps({"rows": rows, "source": "dhan"}, ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp.replace(_CACHE_PATH)
            except Exception:  # noqa: BLE001
                pass
        return True
    finally:
        _refreshing = False


def fetch_heavyweight_snap(blocking: bool = False) -> dict | None:
    """``{rows, source, weights_as_of}`` — non-blocking; sector board untouched."""
    global _refreshing
    import config

    if not config.is_market_open() and not blocking:
        rows = _cache["rows"]
        src = _cache["src"]
        if not valid_heavyweight_rows(rows):
            rows = src = None
            if _CACHE_PATH.exists():
                try:
                    cached = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
                    disk_rows = (cached or {}).get("rows")
                    if valid_heavyweight_rows(disk_rows):
                        rows = disk_rows
                        src = str((cached or {}).get("source") or "cached")
                        _cache.update(at=time.time(), rows=rows, src=src)
                except Exception:  # noqa: BLE001
                    pass
            if not valid_heavyweight_rows(rows):
                _cache.update(rows=None, src=None)
                return None
        return {"rows": rows, "source": src, "weights_as_of": WEIGHTS_AS_OF}

    stale = (time.time() - _cache["at"]) >= _TTL_S
    if blocking:
        _pull()
    elif stale and not _refreshing:
        _refreshing = True
        threading.Thread(target=_pull, daemon=True, name="heavyweight-snap").start()
    if not valid_heavyweight_rows(_cache["rows"]):
        return None
    return {"rows": _cache["rows"], "source": _cache["src"], "weights_as_of": WEIGHTS_AS_OF}


def filter_heavyweight_news(headlines: list | None, *, limit: int = 40) -> list[dict]:
    """Headlines tagged to curated heavyweights (or Corporate & Earnings mentioning them)."""
    syms = set(heavyweight_symbols())
    out: list[dict] = []
    for h in headlines or []:
        if not isinstance(h, dict):
            continue
        related = [str(s) for s in (h.get("related_stocks") or [])]
        hit = [s for s in related if s in syms]
        if not hit:
            continue
        item = dict(h)
        item["related_stocks"] = related
        item["matched_heavyweights"] = hit
        out.append(item)
        if len(out) >= limit:
            break
    return out
