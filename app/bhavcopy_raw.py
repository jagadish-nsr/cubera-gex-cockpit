"""Local store of the FULL raw NIFTY F&O bhavcopy — the complete daily options+futures
history, so every analysis parses offline with no re-fetching.

Each day we keep every NIFTY futures (IDF) and index-option (IDO) row — all strikes, all
expiries — with OHLC, prev-close, settle, OI, ΔOI, volume, and the underlying spot. This is
the raw substrate for PCR, IV-skew, historical GEX, OTM-OI, max-pain, etc. Source: the free
F&O bhavcopy (UDiFF, ~Jul-2024 onward). Volume/OI are in LOTS. ΔOI vs prev day. Sign-free.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_FO_DIR = ROOT / "data" / "journal" / "bhavcopy_fo"

# stored column -> bhavcopy source column (keep the raw fields every study might need)
_COLS = {
    "instr": "FinInstrmTp", "expiry": "XpryDt", "strike": "StrkPric", "opt": "OptnTp",
    "open": "OpnPric", "high": "HghPric", "low": "LwPric", "close": "ClsPric",
    "prev_close": "PrvsClsgPric", "settle": "SttlmPric",
    "oi": "OpnIntrst", "doi": "ChngInOpnIntrst", "volume": "TtlTradgVol",
    "spot": "UndrlygPric",
}
FIELDS = ["date_ist", "symbol", *(_COLS.keys())]


def _path(date_ist: str, symbol: str) -> Path:
    return RAW_FO_DIR / symbol / f"{date_ist}.csv"


def save_raw_fo_day(date_ist: str, text: str, symbol: str = "NIFTY") -> Path | None:
    """Filter a full F&O bhavcopy CSV to ``symbol`` futures+options rows and store the raw
    fields. Idempotent (existing file = no-op). Returns the path, or None if no rows."""
    if not text:
        return None
    import io
    rows = [r for r in csv.DictReader(io.StringIO(text))
            if r.get("TckrSymb") == symbol and r.get("FinInstrmTp") in ("IDF", "IDO")]
    if not rows:
        return None
    path = _path(date_ist, symbol)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            out = {"date_ist": date_ist, "symbol": symbol}
            out.update({k: r.get(src) for k, src in _COLS.items()})
            w.writerow(out)
    return path


def load_raw_fo_day(date_ist: str, symbol: str = "NIFTY"):
    """One day's raw NIFTY F&O rows as a DataFrame (numeric cols coerced), or None."""
    path = _path(date_ist, symbol)
    if not path.exists():
        return None
    import pandas as pd
    df = pd.read_csv(path)
    for c in ("strike", "open", "high", "low", "close", "prev_close", "settle",
              "oi", "doi", "volume", "spot"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df if not df.empty else None


def available_days(symbol: str = "NIFTY") -> list[str]:
    d = RAW_FO_DIR / symbol
    return sorted(p.stem for p in d.glob("*.csv")) if d.exists() else []


def _ist_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def backfill_raw(symbol: str = "NIFTY", n_days: int = 800, today_ist: str | None = None,
                 fetch_fn=None) -> list[str]:
    """Fetch + store the raw F&O bhavcopy for every trading day over the last ``n_days``
    calendar days (today back). Non-trading days 404 and skip; cached days are left as-is
    (idempotent). ``fetch_fn(date)`` returns the bhavcopy CSV text (injectable for a robust
    shared session / tests). Returns dates newly stored, oldest first."""
    if fetch_fn is None:
        from data.nse import fetch_fo_bhavcopy
        fetch_fn = fetch_fo_bhavcopy
    day = datetime.strptime(today_ist or _ist_today(), "%Y-%m-%d").date()
    got: list[str] = []
    for _ in range(n_days):
        date_ist = day.strftime("%Y-%m-%d")
        if not _path(date_ist, symbol).exists():
            text = fetch_fn(day)
            if text and save_raw_fo_day(date_ist, text, symbol):
                got.append(date_ist)
        day = day - timedelta(days=1)
    return sorted(got)
