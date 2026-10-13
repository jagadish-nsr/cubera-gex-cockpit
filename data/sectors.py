"""Sector-index snapshot — each NSE sector index's % change vs the PREVIOUS DAY's close.

A breadth/rotation map for the dashboard: which parts of the market are carrying (or dragging) the
index today. Restricted to the sectors that actually have weight in NIFTY 50 (see SECTORS) — the
question this strip answers is *"what is moving the index I'm trading options on"*, and a sector
with no index constituents cannot answer it. Context only — no signal is derived from it
(consistent with the research arc: sector rotation was never tested here, so it is presented as
information, not an edge).

Two sources, and the choice is deliberate:
- **NSE ``/api/allIndices`` (PRIMARY)** — returns ``percentChange`` and ``previousClose`` for all
  ~139 indices in ONE call. The % change is given by the exchange, not derived by us: nothing to
  get wrong.
- **Dhan ``/marketfeed/quote`` (FALLBACK)** — one call for all sector ids (IDX_I). Dhan has no
  explicit prev-close field, and its two candidates behave differently by session state — now
  **VERIFIED on a live tape** (20-Jul-2026 13:18 IST) and post-close (19-Jul-2026):

  | field | during the session | after hours |
  |---|---|---|
  | ``net_change`` | populated (e.g. −116.3) ✅ | 0 ❌ |
  | ``ohlc.close`` | the PREVIOUS close ✅ | collapses onto ``last_price`` ❌ |

  So intraday either one works (we prefer ``net_change``); after hours neither does, and the
  fallback reports None → "—" rather than silently printing a wrong (or 0.00%) move. The quote
  also carries ``last_trade_time``, which is what makes the session state checkable at all.
"""

from __future__ import annotations

import time

# (NSE index name in /api/allIndices, short display label, Dhan IDX_I security id)
# Ids verified against the Dhan scrip master (SEM_EXM_EXCH_ID=NSE, SEM_INSTRUMENT_NAME=INDEX).
#
# Scope: only sectors that carry REAL WEIGHT IN NIFTY 50 — this is a Nifty options tool, so the
# strip exists to explain what is moving the index being traded. A sector with no index
# constituents can be green all day and change nothing about the Nifty tape, which makes it noise
# on this screen (see EXCLUDED below).
# ⚠ WEIGHTS ARE AN APPROXIMATION, NOT A LIVE FEED. No free NSE/Dhan endpoint publishes index
# weights (checked: /api/allIndices, /api/equity-stockIndices → 404, /api/heatmap-index → prices
# only), so these are read off the published NSE factsheets and are refreshed by hand. They are
# rendered with a leading "~" and this date is shown in the panel, so nobody mistakes them for live.
# They drift a few points a year with rebalances — fine for "Bank is the big one", NOT for maths.
# ⚠ They also OVERLAP and must never be summed: Pvt Bank ⊂ Bank, Oil & Gas ⊂ Energy,
# Pharma ⊂ Healthcare.
WEIGHTS_AS_OF = "2025-07"

# (NSE index name, short label, Dhan IDX_I security id, ~Nifty 50 weight %)
SECTORS: list[tuple[str, str, int, float]] = [
    ("NIFTY BANK", "Bank", 25, 30.0),                  # heaviest block in the index
    ("NIFTY PRIVATE BANK", "Pvt Bank", 15, 27.0),      # the part of it that actually drives Nifty
    ("NIFTY ENERGY", "Energy", 42, 12.0),
    ("NIFTY IT", "IT", 29, 12.0),
    ("NIFTY OIL & GAS", "Oil & Gas", 470, 10.0),       # Reliance-dominated
    ("NIFTY FMCG", "FMCG", 28, 8.0),
    ("NIFTY AUTO", "Auto", 14, 7.0),
    ("NIFTY HEALTHCARE INDEX", "Healthcare", 447, 4.5),
    ("NIFTY METAL", "Metal", 31, 4.0),
    ("NIFTY PHARMA", "Pharma", 32, 4.0),
    ("NIFTY CONSUMER DURABLES", "Cons Durables", 466, 2.0),
]

# Deliberately NOT shown: sector indices with ~no Nifty 50 weight. Kept here (not deleted) so the
# decision is visible and trivially reversible — move a row up into SECTORS to bring it back.
# Realty: no Nifty 50 constituent. PSU Bank: SBI aside, dominated by non-Nifty banks. Media: no
# constituent. Infra: a thematic index that overlaps others rather than adding a distinct read.
EXCLUDED: list[tuple[str, str, int, float]] = [
    ("NIFTY REALTY", "Realty", 34, 0.0),
    ("NIFTY PSU BANK", "PSU Bank", 33, 0.0),
    ("NIFTY MEDIA", "Media", 30, 0.0),
    ("NIFTY INFRASTRUCTURE", "Infra", 43, 0.0),
]

# Broad-market context shown alongside the sectors (same shape).
BROAD: list[tuple[str, str, int, float | None]] = [
    ("NIFTY 50", "Nifty 50", 13, None),
    ("NIFTY MIDCAP 100", "Midcap 100", 37, None),
    ("NIFTY SMALLCAP 100", "Smallcap 100", 5, None),
]

# Sector rotation is slow-moving CONTEXT and must never compete with the option chain / futures
# pull, which are the load-bearing data. Two guards: a long TTL (15 min), and stale-while-
# revalidate — the caller ALWAYS gets the cached rows instantly and the refresh happens on a
# background daemon thread, so no render and no chain fetch ever waits on this.
_TTL_S = 900.0                     # 15 min between successful refreshes
_FAIL_BACKOFF_S = 120.0            # but retry ~2 min after a failure, not 15
_cache: dict = {"at": 0.0, "rows": None, "src": None}
_refreshing = False


def _row(label: str, pct, last, group: str, weight=None) -> dict:
    return {"label": label, "pct": pct, "last": last, "group": group, "weight": weight}


def fetch_sectors_nse(session=None, timeout: int = 10) -> list[dict] | None:
    """All sector + broad rows from NSE ``/api/allIndices`` — ``percentChange`` is the exchange's
    own vs-previous-close number. Returns None on any failure (never raises).

    NOTE: this endpoint answers WITHOUT Akamai priming (verified: 200 in ~0.5s from a bare session),
    so we do NOT pay for ``_primed_session`` here. That matters — its ``/option-chain`` warm-up GET
    hangs outside market hours (>20s), which used to blow the timeout and silently hide the whole
    panel pre-market. Priming is kept only as a fallback for when NSE does demand cookies.
    """
    try:
        import requests
        from data.nse import _ALL_INDICES_URL, _BROWSER_HEADERS
        s = session
        if s is None:
            s = requests.Session()
            s.headers.update(_BROWSER_HEADERS)
        r = s.get(_ALL_INDICES_URL, timeout=timeout)
        if r.status_code != 200 and session is None:      # cookies demanded → prime once and retry
            from data.nse import _primed_session
            s = _primed_session(timeout)
            r = s.get(_ALL_INDICES_URL, timeout=timeout)
        if r.status_code != 200:
            return None
        by = {str(d.get("index", "")).upper(): d for d in (r.json() or {}).get("data", [])}
        out = []
        for group, spec in (("sector", SECTORS), ("broad", BROAD)):
            for name, label, _id, wt in spec:
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
                out.append(_row(label, pct, last, group, wt))
        return out or None
    except Exception:  # noqa: BLE001 - a context panel must never break a fetch
        return None


def fetch_sectors_dhan(timeout: int = 20) -> list[dict] | None:
    """Fallback via one Dhan ``/marketfeed/quote`` for every sector id. A row's ``pct`` is None
    (rendered as '—') whenever the previous close can't be established unambiguously — see the
    module docstring; we do not guess a baseline."""
    try:
        from data.dhan_live import IDX_SEG, _num, _post
        spec = [(label, sid, group, wt)
                for group, rows_ in (("sector", SECTORS), ("broad", BROAD))
                for _name, label, sid, wt in rows_]
        r = _post("/marketfeed/quote", {IDX_SEG: [sid for _l, sid, _g, _w in spec]}, timeout)
        if not r.ok:
            return None
        cells = (r.json() or {}).get("data", {}).get(IDX_SEG, {})
        out = []
        for label, sid, group, wt in spec:
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
            out.append(_row(label, pct, last, group, wt))
        return out or None
    except Exception:  # noqa: BLE001
        return None


def _pull(prefer_dhan: bool = False, session=None) -> bool:
    """One actual fetch into the cache. NSE is primary (it states the % change outright); Dhan is
    the fallback. Returns True if the cache was updated. Never raises."""
    global _refreshing
    try:
        rows = src = None
        order = (("dhan", fetch_sectors_dhan), ("nse", fetch_sectors_nse)) if prefer_dhan else \
                (("nse", lambda: fetch_sectors_nse(session)), ("dhan", fetch_sectors_dhan))
        for name, fn in order:
            rows = fn()
            if rows:
                src = name
                break
        if not rows:
            # Back off on failure so we don't retry every poll — but far shorter than the success
            # TTL, so one bad pull (e.g. an NSE hiccup) can't hide the panel for a whole 15 min.
            _cache["at"] = time.time() - (_TTL_S - _FAIL_BACKOFF_S)
            return False
        rows.sort(key=lambda r: (r["pct"] is None, -(r["pct"] or 0.0)))   # leaders first
        _cache.update(at=time.time(), rows=rows, src=src)
        try:
            from pathlib import Path
            import json
            cache_file = Path("data/.cache/last_sector_snap.json")
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w") as f:
                json.dump({"rows": rows, "source": src}, f)
        except Exception:
            pass
        return True
    finally:
        _refreshing = False


def fetch_sector_snap(prefer_dhan: bool = False, session=None, blocking: bool = False):
    """{rows, source, weights_as_of} for the dashboard strip — **non-blocking by design**.

    Returns whatever is in the cache IMMEDIATELY and, if it is older than ``_TTL_S`` (15 min),
    refreshes it on a background daemon thread. The option chain and futures pull are the
    load-bearing data on this dashboard; this slow-moving context panel must never delay them or
    occupy the fetch path. Consequence: on a cold start the panel is absent for one cycle, then
    appears — an acceptable trade for never competing with the chain.

    ``blocking=True`` forces a synchronous pull (tests / scripts)."""
    global _refreshing
    import config
    # Off market hours: do not make API calls for sector snapshot unless blocking=True (tests/scripts)
    if not config.is_market_open() and not blocking:
        if _cache["rows"] is None:
            from pathlib import Path
            import json
            cache_file = Path("data/.cache/last_sector_snap.json")
            if cache_file.exists():
                try:
                    with open(cache_file, "r") as f:
                        cached_data = json.load(f)
                    if cached_data and "rows" in cached_data:
                        _cache.update(at=time.time(), rows=cached_data["rows"], src=cached_data.get("source", "cached"))
                except Exception:
                    pass
        if _cache["rows"] is None:
            return None
        return {"rows": _cache["rows"], "source": _cache["src"], "weights_as_of": WEIGHTS_AS_OF}

    stale = (time.time() - _cache["at"]) >= _TTL_S
    if blocking:
        _pull(prefer_dhan, session)
    elif stale and not _refreshing:
        import threading
        _refreshing = True
        threading.Thread(target=_pull, args=(prefer_dhan, None), daemon=True,
                         name="sector-snap").start()
    if _cache["rows"] is None:
        return None                            # nothing to show yet (panel simply doesn't render)
    return {"rows": _cache["rows"], "source": _cache["src"], "weights_as_of": WEIGHTS_AS_OF}
