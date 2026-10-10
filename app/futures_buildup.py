"""Nifty (index) FUTURES OI build-up — the sign-free directional positioning read.

Futures give a single OI series and price IS the index, so the classic price+OI matrix
is unambiguous and free of the dealer-*gamma*-sign landmine (a long build-up is a long
build-up):

    price ↑ & OI ↑  = LONG build-up   (fresh longs)        -> bullish
    price ↓ & OI ↑  = SHORT build-up  (fresh shorts)       -> bearish
    price ↑ & OI ↓  = SHORT covering  (shorts exit)        -> bullish, weaker/late
    price ↓ & OI ↓  = LONG unwinding  (longs exit)         -> bearish, weaker
    volume          = conviction/intensity (NOT direction — see the streak IC work)

Daily source: the NSE F&O bhavcopy (EOD, per-contract). This module holds the pure
classifier + a per-symbol daily journal + best-effort fetch/backfill orchestration,
mirroring app/participant_oi.py. Futures OI IS per-index (unlike participant OI), so the
journal is per-symbol. Use the AGGREGATE (across serial months) ΔOI to net out monthly
rollover; near-month ΔOI is distorted around expiry. Decision-support only, sign-free.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Per-index: data/journal/futures/<SYMBOL>/YYYY-MM-DD.csv (one row/day). Gitignored.
FUTURES_DIR = ROOT / "data" / "journal" / "futures"
# Intraday snapshots (live endpoint), one row per snapshot: futures_intraday/<SYMBOL>/.
FUTURES_INTRADAY_DIR = ROOT / "data" / "journal" / "futures_intraday"
FUTURES_INTRADAY_DHAN_DIR = ROOT / "data" / "journal" / "futures_intraday_dhan"  # Dhan feed, separate


def futures_intraday_dir_for(source: str | None) -> Path:
    """Intraday futures journal root for a feed source — Dhan kept separate from NSE (compare later,
    never mix). 'dhan' -> futures_intraday_dhan; NSE / fallback / None -> futures_intraday."""
    return FUTURES_INTRADAY_DHAN_DIR if (source or "").lower().startswith("dhan") else FUTURES_INTRADAY_DIR
FUTURES_INTRADAY_FIELDS = [
    "timestamp_utc", "time_ist", "date_ist", "symbol", "expiry",
    "price", "change", "oi", "volume", "agg_oi", "agg_vol", "spot",
]

FUTURES_FIELDS = [
    "date_ist", "symbol", "expiry",
    "close", "prev_close", "dprice",
    "near_oi", "near_doi", "near_vol",
    "agg_oi", "agg_doi", "agg_vol",
    "spot", "n_contracts",
]
_NUM_FIELDS = FUTURES_FIELDS[3:]

# price+OI matrix -> build-up label; and the directional bias each label implies.
_BUILDUP = {
    ("up", "up"): "long build-up",
    ("down", "up"): "short build-up",
    ("up", "down"): "short covering",
    ("down", "down"): "long unwinding",
}
_BIAS = {
    "long build-up": "bullish",
    "short build-up": "bearish",
    "short covering": "bullish (weaker/late)",
    "long unwinding": "bearish (weaker/late)",
}


def classify_futures_buildup(dprice, doi, tol_price: float = 0.0, tol_doi: float = 0.0) -> str | None:
    """Build-up label from a futures price change and OI change (the price+OI matrix).
    Volume is intensity, not a classifier, so it's not used here. Returns None if
    either input is missing; 'flat' if price or OI barely moved (within tolerance)."""
    if dprice is None or doi is None or dprice != dprice or doi != doi:
        return None
    pu = "up" if dprice > tol_price else "down" if dprice < -tol_price else "flat"
    ou = "up" if doi > tol_doi else "down" if doi < -tol_doi else "flat"
    if pu == "flat" or ou == "flat":
        return "flat"
    return _BUILDUP[(pu, ou)]


def buildup_bias(label: str | None) -> str | None:
    """Directional bias a build-up label implies (bullish/bearish), or None."""
    return _BIAS.get(label) if label else None


def _path_for(date_ist: str, symbol: str) -> Path:
    return FUTURES_DIR / symbol / f"{date_ist}.csv"


def save_futures_day(date_ist: str, parsed: dict) -> Path | None:
    """Write one day's parsed index-futures record (from parse_index_futures) to the
    per-symbol journal as a single row. Idempotent: existing file (final EOD) is a
    no-op. Returns the path, or None if there's nothing/no symbol to save."""
    if not parsed or not parsed.get("symbol"):
        return None
    path = _path_for(date_ist, parsed["symbol"])
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"date_ist": date_ist, "symbol": parsed["symbol"], "expiry": parsed.get("expiry")}
    row.update({k: parsed.get(k) for k in _NUM_FIELDS})
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FUTURES_FIELDS)
        w.writeheader()
        w.writerow(row)
    return path


def load_futures_day(date_ist: str, symbol: str) -> dict | None:
    """Load one day's futures record back into the parse_index_futures shape, or None."""
    path = _path_for(date_ist, symbol)
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return None
    r = rows[0]
    out: dict = {"symbol": r.get("symbol") or symbol, "expiry": r.get("expiry")}
    for k in _NUM_FIELDS:
        v = r.get(k)
        try:
            out[k] = float(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            out[k] = None
    if out.get("n_contracts") is not None:
        out["n_contracts"] = int(out["n_contracts"])
    return out


def available_days(symbol: str) -> list[str]:
    """Sorted IST dates (YYYY-MM-DD) with a captured futures file for ``symbol``."""
    d = FUTURES_DIR / symbol
    return sorted(p.stem for p in d.glob("*.csv")) if d.exists() else []


def futures_series(symbol: str = "NIFTY", n: int = 10, days: list[str] | None = None) -> list[dict]:
    """Per-day futures build-up over the last ``n`` captured days (chronological). Each
    row carries the record plus near-month and AGGREGATE build-up labels + bias. The
    aggregate (rollover-netted) label is the one to trust; near-month is shown for
    contrast around expiry."""
    days = days if days is not None else available_days(symbol)
    out: list[dict] = []
    for d in (days[-n:] if n else days):
        rec = load_futures_day(d, symbol)
        if not rec:
            continue
        near = classify_futures_buildup(rec.get("dprice"), rec.get("near_doi"))
        agg = classify_futures_buildup(rec.get("dprice"), rec.get("agg_doi"))
        out.append({"date": d, **rec,
                    "near_buildup": near, "near_bias": buildup_bias(near),
                    "agg_buildup": agg, "agg_bias": buildup_bias(agg)})
    return out


# --- Intraday capture (live endpoint; forward-only history) --------------------
def _intraday_path(date_ist: str, symbol: str, directory: Path | None = None) -> Path:
    return (directory or FUTURES_INTRADAY_DIR) / symbol / f"{date_ist}.csv"


def _hhmm(t: str) -> int | None:
    try:
        h, m = str(t).split(":"); return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _last_time_ist(path: Path) -> str | None:
    """The time_ist of the last row in an intraday CSV (cheap tail read), or None."""
    try:
        with open(path, encoding="utf-8") as fh:
            last = None
            for line in fh:
                if line.strip():
                    last = line
        if not last or last.startswith("timestamp_utc"):
            return None
        parts = last.rstrip("\n").split(",")
        return parts[1] if len(parts) > 1 else None       # time_ist is column 2
    except OSError:
        return None


def append_futures_snapshot(rec: dict, timestamp_utc: str, time_ist: str, date_ist: str,
                            symbol: str | None = None, min_gap_minutes: float = 0.0,
                            directory: Path | None = None):
    """Append one live futures snapshot (from parse_futures_live) to the intraday
    journal. ``min_gap_minutes`` skips the write if the last row is younger than that
    (keeps a best-effort dashboard capture from double-writing between logger ticks).
    ``directory`` routes to the feed's own journal (Dhan vs NSE). Returns (path, written)."""
    if not rec:
        return None, False
    symbol = symbol or rec.get("symbol")
    if not symbol:
        return None, False
    path = _intraday_path(date_ist, symbol, directory)
    if min_gap_minutes and path.exists():
        last = _last_time_ist(path)
        a, b = _hhmm(last), _hhmm(time_ist)
        if a is not None and b is not None and (b - a) < min_gap_minutes:
            return path, False
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"timestamp_utc": timestamp_utc, "time_ist": time_ist,
           "date_ist": date_ist, "symbol": symbol}
    row.update({k: rec.get(k) for k in FUTURES_INTRADAY_FIELDS[4:]})
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FUTURES_INTRADAY_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    return path, True


def load_futures_intraday_day(date_ist: str, symbol: str, directory: Path | None = None):
    """Today's intraday futures snapshots as a DataFrame (sorted by time), or None."""
    path = _intraday_path(date_ist, symbol, directory)
    if not path.exists():
        return None
    import pandas as pd
    df = pd.read_csv(path)
    return df.sort_values("time_ist") if not df.empty else None


def capture_futures_snapshot(symbol: str = "NIFTY", now_utc: datetime | None = None,
                             fetch_fn=None, parse_fn=None, min_gap_minutes: float = 0.0,
                             directory: Path | None = None):
    """Best-effort: fetch the live futures quote and append an intraday snapshot.
    Returns (path, written) or None if unreachable/unsupported symbol. fetch_fn/parse_fn
    injectable (default NSE live; pass Dhan's fetch_futures_dhan + an identity parse for the Dhan
    feed). ``directory`` routes to the feed's own journal."""
    if fetch_fn is None or parse_fn is None:
        from data.nse import fetch_futures_live, parse_futures_live
        fetch_fn = fetch_fn or fetch_futures_live
        parse_fn = parse_fn or parse_futures_live
    payload = fetch_fn(symbol)
    rec = parse_fn(payload, symbol) if payload else None
    if not rec:
        return None
    now = now_utc or datetime.now(timezone.utc)
    ist = now + timedelta(hours=5, minutes=30)
    return append_futures_snapshot(rec, now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   ist.strftime("%H:%M"), ist.strftime("%Y-%m-%d"),
                                   symbol, min_gap_minutes, directory=directory)


def capture_futures_from_ws(symbol: str = "NIFTY", now_utc: datetime | None = None,
                             min_gap_minutes: float = 0.0,
                             directory: Path | None = None,
                             fetch_fn=None, parse_fn=None):
    """Fast path: append an intraday futures snapshot directly from the WebSocket in-memory
    store (``app.spot_ticker._STORE[symbol]["fut"]``) — **zero network calls**.

    The WS feed (``app.ws_spot_feed``) already holds the near-month futures LTP, OI, volume and
    expiry decoded from the Dhan binary packet; there is no need to re-fetch via REST.  Falls back
    to ``capture_futures_snapshot()`` (the REST path) when the WS store is absent or stale (e.g.
    outside market hours, or before the WS thread has warmed up).

    Returns (path, written) or None — same contract as ``capture_futures_snapshot``."""
    import time as _t
    try:
        from app.spot_ticker import _STORE as _spot_store, STALE_AFTER_S as _stale
        _rec = _spot_store.get(symbol)
        _ws_at = (_rec or {}).get("ws_at") or 0
        _at = (_rec or {}).get("at") or 0
        _fut = (_rec or {}).get("fut")
        _fresh = (_t.time() - max(_ws_at, _at)) < _stale
        if _fut and _fresh:
            # In-memory store is fresh — write the snapshot without touching the network
            now = now_utc or datetime.now(timezone.utc)
            ist = now + timedelta(hours=5, minutes=30)
            return append_futures_snapshot(
                _fut, now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                ist.strftime("%H:%M"), ist.strftime("%Y-%m-%d"),
                symbol, min_gap_minutes, directory=directory,
            )
    except Exception:  # noqa: BLE001 - fast path failed; fall through to REST
        pass
    # WS store absent / stale → fall back to the regular REST-based snapshot
    if fetch_fn is None and directory and "dhan" in str(directory).lower():
        from data.dhan_live import fetch_futures_dhan
        fetch_fn = fetch_futures_dhan
        parse_fn = lambda p, s: p
    return capture_futures_snapshot(symbol, now_utc=now_utc,
                                    fetch_fn=fetch_fn, parse_fn=parse_fn,
                                    min_gap_minutes=min_gap_minutes, directory=directory)



def intraday_futures_buildup(day_df) -> dict | None:
    """Today's INTRADAY futures build-up from the first vs latest captured snapshot:
    ΔPrice (near-month) + ΔOI (aggregate, rollover-netted) + Δvolume over the session,
    classified via the same matrix. Uses aggregate OI so intraday rollover doesn't
    distort it. None if fewer than 2 snapshots. Sign-free."""
    if day_df is None or getattr(day_df, "empty", True):
        return None
    import pandas as pd
    df = day_df.sort_values("time_ist").drop_duplicates("time_ist")
    if len(df) < 2:
        return None
    first, last = df.iloc[0], df.iloc[-1]

    def _f(r, k):
        v = pd.to_numeric(r.get(k), errors="coerce")
        return float(v) if v == v else None

    p0, p1 = _f(first, "price"), _f(last, "price")
    o0, o1 = _f(first, "agg_oi"), _f(last, "agg_oi")
    v0, v1 = _f(first, "agg_vol"), _f(last, "agg_vol")
    dprice = (p1 - p0) if (p0 is not None and p1 is not None) else None
    doi = (o1 - o0) if (o0 is not None and o1 is not None) else None
    dvol = (v1 - v0) if (v0 is not None and v1 is not None) else None
    label = classify_futures_buildup(dprice, doi)
    return {"dprice": dprice, "doi": doi, "dvol": dvol,
            # strength %s (both unit-consistent, live feed): OI build since open, and
            # turnover = today's volume ÷ current OI (conviction). NOT vs bhavcopy (lots≠units).
            "doi_pct": (doi / o0 * 100.0) if (doi is not None and o0) else None,
            "turnover_pct": (dvol / o1 * 100.0) if (dvol is not None and o1) else None,
            "buildup": label, "bias": buildup_bias(label),
            "price_now": p1, "oi_now": o1, "spot_now": _f(last, "spot"),
            "start_time": first.get("time_ist"), "end_time": last.get("time_ist"),
            "n": len(df)}


def latest_intraday_snapshot(day_df) -> dict | None:
    """The latest captured intraday snapshot's LEVELS (price + change-vs-prev-close, OI,
    volume, spot) — a partial read for when only ONE snapshot exists (a build-up delta
    needs 2). ``change`` comes straight from the feed, so price direction is known even
    from a single snapshot; OI/volume are levels (no Δ yet). None if empty."""
    if day_df is None or getattr(day_df, "empty", True):
        return None
    import pandas as pd
    df = day_df.sort_values("time_ist").drop_duplicates("time_ist")
    r = df.iloc[-1]

    def _f(k):
        v = pd.to_numeric(r.get(k), errors="coerce")
        return float(v) if v == v else None

    return {"time_ist": r.get("time_ist"), "price": _f("price"), "change": _f("change"),
            "agg_oi": _f("agg_oi"), "agg_vol": _f("agg_vol"), "spot": _f("spot"), "n": len(df)}


def intraday_futures_steps(day_df) -> list[dict]:
    """Per-interval intraday build-up trajectory: between consecutive snapshots, classify
    from (near-month price step, AGGREGATE OI step). Returns [{time, dprice, doi, dvol,
    state}] oldest→newest — how the futures build-up EVOLVED through the session (the
    substrate for the intraday-direction study). Aggregate OI so intraday rollover doesn't
    distort it. Empty if < 2 snapshots. Sign-free."""
    if day_df is None or getattr(day_df, "empty", True):
        return []
    import pandas as pd
    df = day_df.sort_values("time_ist").drop_duplicates("time_ist")
    if len(df) < 2:
        return []
    t = df["time_ist"].tolist()
    price = pd.to_numeric(df["price"], errors="coerce").to_numpy()
    oi = pd.to_numeric(df["agg_oi"], errors="coerce").to_numpy()
    vol = pd.to_numeric(df["agg_vol"], errors="coerce").to_numpy()

    def _d(a, b):
        return float(a - b) if (a == a and b == b) else None

    out = []
    for i in range(1, len(df)):
        dp, do = _d(price[i], price[i - 1]), _d(oi[i], oi[i - 1])
        prev_oi = oi[i - 1] if oi[i - 1] == oi[i - 1] else None
        out.append({"time": t[i], "dprice": dp, "doi": do,
                    "doi_pct": (do / prev_oi * 100.0) if (do is not None and prev_oi) else None,
                    "dvol": _d(vol[i], vol[i - 1]),
                    "state": classify_futures_buildup(dp, do)})
    return out


def _ist_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _fetch_parse(fetch_fn, parse_fn):
    if fetch_fn is None or parse_fn is None:
        from data.nse import fetch_fo_bhavcopy, parse_index_futures
        fetch_fn = fetch_fn or fetch_fo_bhavcopy
        parse_fn = parse_fn or parse_index_futures
    return fetch_fn, parse_fn


def capture_latest(symbol: str = "NIFTY", today_ist: str | None = None,
                   fetch_fn=None, parse_fn=None, max_back: int = 4) -> tuple[str, dict] | None:
    """Best-effort: fetch the newest available futures bhavcopy and cache it. Tries
    ``today_ist``, then walks back up to ``max_back`` days (weekend/holiday/not-yet-
    published all 404). Returns (date_ist, parsed) on the first day that parses (cached,
    idempotent), or None. fetch_fn/parse_fn injectable for tests."""
    fetch_fn, parse_fn = _fetch_parse(fetch_fn, parse_fn)
    day = datetime.strptime(today_ist or _ist_today(), "%Y-%m-%d").date()
    for _ in range(max_back + 1):
        date_ist = day.strftime("%Y-%m-%d")
        cached = load_futures_day(date_ist, symbol)
        if cached:
            return (date_ist, cached)
        text = fetch_fn(day)
        parsed = parse_fn(text, symbol) if text else None
        if parsed:
            save_futures_day(date_ist, parsed)
            return (date_ist, parsed)
        day = day - timedelta(days=1)
    return None


def backfill(symbol: str = "NIFTY", n_days: int = 30, today_ist: str | None = None,
             fetch_fn=None, parse_fn=None) -> list[str]:
    """Fetch + cache the futures bhavcopy for every available day over the last
    ``n_days`` calendar days (today back). Non-trading days 404 and are skipped;
    already-cached days are left as-is (idempotent). Returns dates newly captured this
    run, oldest first — fills the daily build-up history for the IC study."""
    fetch_fn, parse_fn = _fetch_parse(fetch_fn, parse_fn)
    day = datetime.strptime(today_ist or _ist_today(), "%Y-%m-%d").date()
    captured: list[str] = []
    for _ in range(n_days):
        date_ist = day.strftime("%Y-%m-%d")
        if load_futures_day(date_ist, symbol) is None:
            text = fetch_fn(day)
            parsed = parse_fn(text, symbol) if text else None
            if parsed:
                save_futures_day(date_ist, parsed)
                captured.append(date_ist)
        day = day - timedelta(days=1)
    return sorted(captured)
