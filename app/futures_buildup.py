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
