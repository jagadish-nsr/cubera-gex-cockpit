"""Sector-index snapshot — each NSE sector index's % change vs the PREVIOUS DAY's close.

A breadth/rotation map for the dashboard: which parts of the market are carrying (or dragging) the
index today. Context only — no signal is derived from it (consistent with the research arc: sector
rotation was never tested here, so it is presented as information, not an edge).

Two sources, and the choice is deliberate:
- **NSE ``/api/allIndices`` (PRIMARY)** — returns ``percentChange`` and ``previousClose`` for all
  ~139 indices in ONE call. The % change is given by the exchange, not derived by us: nothing to
  get wrong.
- **Dhan ``/marketfeed/quote`` (FALLBACK)** — one call for all sector ids (IDX_I). Dhan gives no
  explicit prev-close field: ``net_change`` reads 0 on this feed and ``ohlc.close`` is AMBIGUOUS
  (it is the previous close during the session, but becomes TODAY's close after hours — verified
  post-close: close == last_price exactly). So the fallback reports a % only when it can be derived
  unambiguously, and reports None otherwise rather than silently printing a wrong (or 0.00%) move.
  ⚠ VERIFY-LIVE: the in-session meaning of ``ohlc.close`` has not been confirmed on a live tape.
"""

from __future__ import annotations

import time

# (NSE index name in /api/allIndices, short display label, Dhan IDX_I security id)
# Ids verified against the Dhan scrip master (SEM_EXM_EXCH_ID=NSE, SEM_INSTRUMENT_NAME=INDEX).
SECTORS: list[tuple[str, str, int]] = [
    ("NIFTY BANK", "Bank", 25),
    ("NIFTY IT", "IT", 29),
    ("NIFTY AUTO", "Auto", 14),
    ("NIFTY PHARMA", "Pharma", 32),
    ("NIFTY FMCG", "FMCG", 28),
    ("NIFTY METAL", "Metal", 31),
    ("NIFTY ENERGY", "Energy", 42),
    ("NIFTY OIL & GAS", "Oil & Gas", 470),
    ("NIFTY REALTY", "Realty", 34),
    ("NIFTY PSU BANK", "PSU Bank", 33),
    ("NIFTY PRIVATE BANK", "Pvt Bank", 15),
    ("NIFTY HEALTHCARE INDEX", "Healthcare", 447),
    ("NIFTY CONSUMER DURABLES", "Cons Durables", 466),
    ("NIFTY MEDIA", "Media", 30),
    ("NIFTY INFRASTRUCTURE", "Infra", 43),
]

# Broad-market context shown alongside the sectors (same shape).
BROAD: list[tuple[str, str, int]] = [
    ("NIFTY 50", "Nifty 50", 13),
    ("NIFTY MIDCAP 100", "Midcap 100", 37),
    ("NIFTY SMALLCAP 100", "Smallcap 100", 5),
]

_TTL_S = 60.0                      # sectors move slowly; don't hammer either feed
_cache: dict = {"at": 0.0, "rows": None, "src": None}


def _row(label: str, pct, last, group: str) -> dict:
    return {"label": label, "pct": pct, "last": last, "group": group}


def fetch_sectors_nse(session=None, timeout: int = 10) -> list[dict] | None:
    """All sector + broad rows from NSE ``/api/allIndices`` — ``percentChange`` is the exchange's
    own vs-previous-close number. Returns None on any failure (never raises)."""
    try:
        from data.nse import _ALL_INDICES_URL, _primed_session
        s = session or _primed_session(timeout)
        r = s.get(_ALL_INDICES_URL, timeout=timeout)
        if r.status_code != 200:
            return None
        by = {str(d.get("index", "")).upper(): d for d in (r.json() or {}).get("data", [])}
        out = []
        for group, spec in (("sector", SECTORS), ("broad", BROAD)):
            for name, label, _id in spec:
                d = by.get(name.upper())
                if not d:
                    continue
                try:
                    pct = float(d["percentChange"])
                except (KeyError, TypeError, ValueError):
                    continue
                try:
                    last = float(d.get("last"))
                except (TypeError, ValueError):
                    last = None
                out.append(_row(label, pct, last, group))
        return out or None
    except Exception:  # noqa: BLE001 - a context panel must never break a fetch
        return None


def fetch_sectors_dhan(timeout: int = 20) -> list[dict] | None:
    """Fallback via one Dhan ``/marketfeed/quote`` for every sector id. A row's ``pct`` is None
    (rendered as '—') whenever the previous close can't be established unambiguously — see the
    module docstring; we do not guess a baseline."""
    try:
        from data.dhan_live import IDX_SEG, _num, _post
        spec = [(label, sid, group)
                for group, rows_ in (("sector", SECTORS), ("broad", BROAD))
                for _name, label, sid in rows_]
        r = _post("/marketfeed/quote", {IDX_SEG: [sid for _l, sid, _g in spec]}, timeout)
        if not r.ok:
            return None
        cells = (r.json() or {}).get("data", {}).get(IDX_SEG, {})
        out = []
        for label, sid, group in spec:
            c = cells.get(str(sid))
            if not c:
                continue
            last = _num(c.get("last_price") or c.get("ltp"))
            chg = _num(c.get("net_change")) or None
            if not chg:
                prev = _num((c.get("ohlc") or {}).get("close"))
                # close == last ⇒ it is TODAY's close (post-session), not a baseline: refuse it.
                chg = (last - prev) if (last and prev and abs(last - prev) > 1e-9) else None
            base = (last - chg) if (last is not None and chg is not None) else None
            pct = (chg / base * 100.0) if (base not in (None, 0.0) and chg is not None) else None
            out.append(_row(label, pct, last, group))
        return out or None
    except Exception:  # noqa: BLE001
        return None


def fetch_sector_snap(prefer_dhan: bool = False, session=None) -> dict | None:
    """{rows, source} for the dashboard strip, sorted by % change (leaders → laggards), cached for
    ``_TTL_S``. NSE is primary because it states the % change outright; Dhan is the fallback (and
    is tried first only when the NSE path is unavailable to the caller). Best-effort: None if both
    sources fail — the panel then simply doesn't render."""
    now = time.time()
    if _cache["rows"] is not None and (now - _cache["at"]) < _TTL_S:
        return {"rows": _cache["rows"], "source": _cache["src"]}
    rows = src = None
    order = (("dhan", fetch_sectors_dhan), ("nse", fetch_sectors_nse)) if prefer_dhan else \
            (("nse", lambda: fetch_sectors_nse(session)), ("dhan", fetch_sectors_dhan))
    for name, fn in order:
        rows = fn()
        if rows:
            src = name
            break
    if not rows:
        return None
    rows.sort(key=lambda r: (r["pct"] is None, -(r["pct"] or 0.0)))   # leaders first, unknown last
    _cache.update(at=now, rows=rows, src=src)
    return {"rows": rows, "source": src}
