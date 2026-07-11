"""EOD per-strike index-option OI from the F&O bhavcopy — the FREE, deep historical
source for study #4 (OTM option-OI growth → next-day return; Zhou/Seo).

Each daily bhavcopy carries every option strike's OI/ΔOI/volume + the underlying spot
(UndrlygPric), going back months–years. This module backfills a compact near-money
journal (one CSV/day, ±BAND strikes) so the daily study runs offline at full sample.
Mirrors app/futures_buildup.py. Sign-free; ΔOI vs prev day, in lots.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPTIONS_EOD_DIR = ROOT / "data" / "journal" / "options_eod"
STORE_BAND_FRAC = 0.08          # persist near-money strikes only (±8% of spot)

FIELDS = ["date_ist", "symbol", "spot", "strike",
          "ce_oi", "ce_doi", "ce_vol", "pe_oi", "pe_doi", "pe_vol"]
_SFIELDS = FIELDS[4:]


def _path(date_ist: str, symbol: str) -> Path:
    return OPTIONS_EOD_DIR / symbol / f"{date_ist}.csv"


def save_options_day(date_ist: str, parsed: dict, band_frac: float = STORE_BAND_FRAC) -> Path | None:
    """Write one day's near-money per-strike option OI (from parse_index_options) to the
    journal. Idempotent (existing file = no-op). Bands to ±band_frac of spot to stay small."""
    if not parsed or not parsed.get("strikes") or not parsed.get("spot"):
        return None
    symbol, spot = parsed.get("symbol", "NIFTY"), float(parsed["spot"])
    path = _path(date_ist, symbol)
    if path.exists():
        return path
    band = spot * band_frac
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for k, v in sorted(parsed["strikes"].items()):
            if abs(float(k) - spot) > band:
                continue
            row = {"date_ist": date_ist, "symbol": symbol, "spot": spot, "strike": float(k)}
            row.update({f: v.get(f) for f in _SFIELDS})
            w.writerow(row)
    return path


def load_options_day(date_ist: str, symbol: str) -> dict | None:
    """Load one day back into ``{spot, strikes: {k: {...}}}``, or None."""
    path = _path(date_ist, symbol)
    if not path.exists():
        return None
    strikes, spot = {}, None
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                k = float(r["strike"]); spot = float(r["spot"])
            except (KeyError, ValueError, TypeError):
                continue
            strikes[k] = {f: (float(r[f]) if r.get(f) not in (None, "") else 0.0) for f in _SFIELDS}
    return {"symbol": symbol, "spot": spot, "strikes": strikes} if strikes else None


def available_days(symbol: str) -> list[str]:
    d = OPTIONS_EOD_DIR / symbol
    return sorted(p.stem for p in d.glob("*.csv")) if d.exists() else []


def daily_spot(symbol: str) -> dict[str, float]:
    """{date_ist: spot (UndrlygPric = NIFTY close)} across the journal — the deep daily
    close series for next-day returns (used by studies #4 and #5)."""
    out = {}
    for d in available_days(symbol):
        rec = load_options_day(d, symbol)
        if rec and rec.get("spot"):
            out[d] = float(rec["spot"])
    return out


def otm_oi_signal(rec: dict, band_frac: float = 0.03) -> dict | None:
    """EOD OTM option-OI GROWTH signal from a loaded day: near-OTM call ΔOI (strike>spot)
    vs near-OTM put ΔOI (strike<spot), within ±band_frac. Adds-only (positive ΔOI)."""
    if not rec or not rec.get("spot") or not rec.get("strikes"):
        return None
    spot = float(rec["spot"]); band = spot * band_frac
    cg = pg = 0.0
    for k, v in rec["strikes"].items():
        if k > spot and k <= spot + band:
            cg += max(v.get("ce_doi") or 0.0, 0.0)
        elif k < spot and k >= spot - band:
            pg += max(v.get("pe_doi") or 0.0, 0.0)
    tot = cg + pg
    return {"otm_call_growth": cg, "otm_put_growth": pg,
            "net_otm": (cg - pg) / tot if tot else None,
            "otm_pcr_doi": pg / cg if cg else None}


def _ist_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def backfill(symbol: str = "NIFTY", n_days: int = 400, today_ist: str | None = None,
             fetch_fn=None, parse_fn=None) -> list[str]:
    """Fetch + cache the near-money option EOD for every available bhavcopy over the last
    ``n_days`` calendar days (today back). Non-trading days 404 and skip; cached days are
    left as-is (idempotent). Returns dates newly captured, oldest first."""
    if fetch_fn is None or parse_fn is None:
        from data.nse import fetch_fo_bhavcopy, parse_index_options
        fetch_fn = fetch_fn or fetch_fo_bhavcopy
        parse_fn = parse_fn or parse_index_options
    day = datetime.strptime(today_ist or _ist_today(), "%Y-%m-%d").date()
    got: list[str] = []
    for _ in range(n_days):
        date_ist = day.strftime("%Y-%m-%d")
        if load_options_day(date_ist, symbol) is None:
            text = fetch_fn(day)
            parsed = parse_fn(text, symbol) if text else None
            if parsed and save_options_day(date_ist, parsed):
                got.append(date_ist)
        day = day - timedelta(days=1)
    return sorted(got)
