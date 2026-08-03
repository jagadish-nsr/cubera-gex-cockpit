"""Phase A — intraday GEX time-series: storage, row-building, loading.

One row per snapshot, one CSV per IST trading day under
``data/journal/timeseries/YYYY-MM-DD.csv`` (the whole ``data/journal/`` tree is
gitignored — personal data). Shared by ``scripts/log_timeseries.py`` (the writer)
and the dashboard (reader / chart) so the capture format lives in ONE place.

Each row carries the 5 GEX levels + spot + regime, the OI/volume build-up
aggregates, and full provenance (source, dealer-sign model, staleness). The
engine schema is untouched — the OI/volume aggregates are computed off the raw
NSE payload in ``data/nse.aggregate_oi_volume``. Decision-support only.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import config

ROOT = Path(__file__).resolve().parents[1]
# Each capture stream is namespaced by index under a per-symbol SUBFOLDER, e.g.
# data/journal/timeseries/NIFTY/2026-06-29.csv and .../BANKNIFTY/... — so the two
# indices never mix in one file. ``symbol=None`` resolves to config.DEFAULT_INDEX.
TS_DIR = ROOT / "data" / "journal" / "timeseries"
# Per-strike OI/ΔOI capture (long format: one row per strike per snapshot).
OISTRIKES_DIR = ROOT / "data" / "journal" / "oi_strikes"          # NSE (+ legacy) feed
OISTRIKES_DHAN_DIR = ROOT / "data" / "journal" / "oi_strikes_dhan"  # Dhan feed — kept SEPARATE


def oistrikes_dir_for(source: str | None) -> Path:
    """The per-strike journal root for a feed source. Dhan is kept in its OWN tree so the two
    feeds never mix and can be compared later. 'dhan' -> oi_strikes_dhan; NSE / Dhan-NSE-fallback /
    None / demo -> oi_strikes."""
    return OISTRIKES_DHAN_DIR if (source or "").lower().startswith("dhan") else OISTRIKES_DIR


def gamma_blast_dir_for(source: str | None) -> Path:
    """The 1-min gamma-blast capture root for a feed source — Dhan kept separate (never mixed).
    'dhan' -> gamma_blast_dhan; NSE / fallback / None / demo -> gamma_blast."""
    return GAMMA_BLAST_DHAN_DIR if (source or "").lower().startswith("dhan") else GAMMA_BLAST_DIR
# Expiry-day 1-min capture for the gamma-blast study (same per-strike schema,
# finer cadence, bounded window) — kept separate so it doesn't mix cadences.
GAMMA_BLAST_DIR = ROOT / "data" / "journal" / "gamma_blast"
GAMMA_BLAST_DHAN_DIR = ROOT / "data" / "journal" / "gamma_blast_dhan"  # Dhan feed, separate


def _sym(symbol: str | None) -> str:
    """Resolve a capture symbol (None -> the default index), upper-cased."""
    return (symbol or config.DEFAULT_INDEX).upper()


def _stream_dir(base: Path, symbol: str | None) -> Path:
    """The per-symbol subfolder of a capture stream (base / SYMBOL)."""
    return base / _sym(symbol)

# One row per strike per snapshot. Keep this list and build_strike_rows() in step.
# call_ltp/put_ltp = nearest-expiry mid premiums (for the crossover watch);
# net_gex = engine gamma exposure at the strike (for the Δgamma / blast-strike map).
STRIKE_FIELDS = [
    "timestamp_utc", "time_ist", "date_ist", "spot", "strike",
    "call_oi", "call_doi", "put_oi", "put_doi", "call_vol", "put_vol",
    "call_ltp", "put_ltp", "net_gex",
    # Nearest-expiry OI-change + premium-change, both vs prev close (for the EXACT
    # build-up tag). Appended last so old CSVs stay readable (missing => optional).
    "call_doi_near", "put_doi_near", "call_dprem", "put_dprem",
    # Nearest-expiry ABSOLUTE OI — a source-consistent series (live + Dhan backfill) that the
    # trailing windows difference (baseline cancels → the window's nearest-expiry OI change).
    "call_oi_near", "put_oi_near",
]

# One row per snapshot. Keep this list and build_row() in lock-step.
FIELDS = [
    "timestamp_utc", "time_ist", "date_ist", "spot",
    "regime", "mode_label", "flip",
    "call_wall", "put_wall", "magnet", "max_pain", "net_gex",
    # OI / volume build-up (off the raw payload; vs previous close for ΔOI)
    "call_oi", "put_oi", "pcr_oi", "call_doi", "put_doi", "call_vol", "put_vol",
    # India VIX (implied vol; independent of our dealer-sign assumption)
    "india_vix", "vix_pct_chg",
    # USD/INR near-month future (FX context; sign-assumption-free)
    "usdinr", "usdinr_pct_chg",
    # News-intensity (free RSS; flags exogenous-catalyst days for Phase B)
    "news_count", "news_impact", "news_top",
    # provenance
    "dealer_sign_model", "expiry_weighting", "n_contracts", "source", "stale",
]


# session window from config (single source; F&O extended to 15:40 on 2026-08-03). getattr fallbacks so a
# hot-reload against a stale cached `config` degrades gracefully instead of crashing (restart to refresh).
_open_min = getattr(config, "MARKET_OPEN_MIN", 9 * 60 + 15)
_close_min = getattr(config, "FNO_CLOSE_MIN", 15 * 60 + 40)
_OPEN_IST = (_open_min // 60, _open_min % 60)      # 09:15
_CLOSE_IST = (_close_min // 60, _close_min % 60)   # 15:40 (option chain = F&O)


def _ist(now_utc: datetime) -> datetime:
    return now_utc + timedelta(hours=5, minutes=30)


def in_nse_session(now_utc: datetime) -> bool:
    """True if a UTC instant falls inside the NSE cash session (Mon-Fri,
    09:15-15:30 IST). Holiday-unaware. Single source for the session window so
    the writer (scheduler), the dashboard auto-save guard, and the offline
    analyser all agree on what counts as an in-session snapshot."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    ist = _ist(now_utc)
    if ist.weekday() >= 5:
        return False
    minute = ist.hour * 60 + ist.minute
    return _OPEN_IST[0] * 60 + _OPEN_IST[1] <= minute <= _CLOSE_IST[0] * 60 + _CLOSE_IST[1]


def _f(x, nd=None):
    """Format a numeric cell: None/NaN -> "" (blank), else value (optionally rounded)."""
    if x is None:
        return ""
    try:
        if x != x:  # NaN
            return ""
    except TypeError:
        return x
    return round(x, nd) if nd is not None else x


def _news_top(s) -> str:
    """One-line, length-capped headline for the CSV (commas are quoted by csv)."""
    if not s:
        return ""
    return " ".join(str(s).split())[:120]


def ts_path_for(date_ist: str, symbol: str | None = None) -> Path:
    # Resolve TS_DIR at CALL time (tests monkeypatch it; a default-arg would bind
    # the old value at def-time — see strikes_path_for for the same guard).
    return _stream_dir(TS_DIR, symbol) / f"{date_ist}.csv"


def available_days(symbol: str | None = None) -> list[str]:
    """Sorted IST dates (YYYY-MM-DD) with a time-series file for this index."""
    d = _stream_dir(TS_DIR, symbol)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.csv"))


def build_row(data) -> dict:
    """Flatten a DashboardData bundle into one time-series row (keys == FIELDS)."""
    L, V = data.levels, data.view
    fa = data.fetched_at
    if fa.tzinfo is None:
        fa = fa.replace(tzinfo=timezone.utc)
    ist = _ist(fa)
    ov = data.oi_volume or {}
    return {
        "timestamp_utc": fa.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_ist": ist.strftime("%H:%M"),
        "date_ist": ist.strftime("%Y-%m-%d"),
        "spot": round(L.spot, 2),
        "regime": L.regime,
        "mode_label": V.mode_label,
        "flip": round(L.flip, 1) if L.flip else "",
        "call_wall": int(L.call_wall),
        "put_wall": int(L.put_wall),
        "magnet": int(L.magnet),
        "max_pain": int(L.max_pain),
        "net_gex": int(L.total_net_gex),
        "call_oi": _f(ov.get("call_oi")),
        "put_oi": _f(ov.get("put_oi")),
        "pcr_oi": _f(ov.get("pcr_oi"), 3),
        "call_doi": _f(ov.get("call_doi")),
        "put_doi": _f(ov.get("put_doi")),
        "call_vol": _f(ov.get("call_vol")),
        "put_vol": _f(ov.get("put_vol")),
        "india_vix": _f((data.india_vix or {}).get("last"), 2),
        "vix_pct_chg": _f((data.india_vix or {}).get("pct_change"), 2),
        "usdinr": _f((data.usdinr or {}).get("last"), 4),
        "usdinr_pct_chg": _f((data.usdinr or {}).get("pct_change"), 2),
        "news_count": _f((data.news or {}).get("count")),
        "news_impact": _f((data.news or {}).get("impact")),
        "news_top": _news_top((data.news or {}).get("top")),
        "dealer_sign_model": L.dealer_sign_model,
        "expiry_weighting": L.expiry_weighting,
        "n_contracts": data.n_contracts,
        "source": data.source,
        "stale": L.oi_stale,
    }


def build_strike_rows(data) -> list[dict]:
    """Flatten a DashboardData bundle's per-strike OI into long-format rows
    (keys == STRIKE_FIELDS). Empty list if the bundle carries no per-strike OI
    (e.g. the demo path, which has no raw NSE payload)."""
    strikes = data.oi_strikes or []
    if not strikes:
        return []
    fa = data.fetched_at
    if fa.tzinfo is None:
        fa = fa.replace(tzinfo=timezone.utc)
    ist = _ist(fa)
    ts_utc = fa.strftime("%Y-%m-%dT%H:%M:%SZ")
    t_ist = ist.strftime("%H:%M")
    d_ist = ist.strftime("%Y-%m-%d")
    spot = round(data.levels.spot, 2)
    rows = []
    for s in strikes:
        rows.append({
            "timestamp_utc": ts_utc, "time_ist": t_ist, "date_ist": d_ist,
            "spot": spot, "strike": _f(s.get("strike")),
            "call_oi": _f(s.get("call_oi")), "call_doi": _f(s.get("call_doi")),
            "put_oi": _f(s.get("put_oi")), "put_doi": _f(s.get("put_doi")),
            "call_vol": _f(s.get("call_vol")), "put_vol": _f(s.get("put_vol")),
            "call_ltp": _f(s.get("call_ltp"), 2), "put_ltp": _f(s.get("put_ltp"), 2),
            "net_gex": _f(s.get("net_gex")),
            "call_doi_near": _f(s.get("call_doi_near")),
            "put_doi_near": _f(s.get("put_doi_near")),
            "call_dprem": _f(s.get("call_dprem"), 2),
            "put_dprem": _f(s.get("put_dprem"), 2),
            "call_oi_near": _f(s.get("call_oi_near")),
            "put_oi_near": _f(s.get("put_oi_near")),
        })
    return rows


def strikes_path_for(date_ist: str, symbol: str | None = None,
                     directory: Path | None = None) -> Path:
    # Resolve the module global at CALL time (default-arg binding would capture it
    # at def-time and break monkeypatch in tests / any reassignment of OISTRIKES_DIR).
    return _stream_dir(directory or OISTRIKES_DIR, symbol) / f"{date_ist}.csv"


def append_strike_rows(rows: list[dict], date_ist: str | None = None,
                       symbol: str | None = None,
                       directory: Path | None = None) -> Path | None:
    """Append per-strike rows to that IST day's CSV under ``directory/SYMBOL``
    (header on a new file). No-op returning None if there are no rows.
    ``directory`` lets the 1-min gamma-blast logger write to its own folder with
    the same schema; None => OISTRIKES_DIR. ``symbol`` picks the index subfolder
    (None => default index)."""
    if not rows:
        return None
    date_ist = date_ist or rows[0]["date_ist"]
    path = strikes_path_for(date_ist, symbol, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=STRIKE_FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)
    return path


def load_strikes_day(date_ist: str, symbol: str | None = None,
                     directory: Path | None = None) -> pd.DataFrame:
    """Load one IST day's per-strike capture (empty frame if none yet).
    directory=None => OISTRIKES_DIR; symbol=None => default index (resolved now)."""
    path = strikes_path_for(date_ist, symbol, directory)
    if not path.exists():
        return pd.DataFrame(columns=STRIKE_FIELDS)
    return pd.read_csv(path)


def append_row(row: dict, date_ist: str | None = None, symbol: str | None = None) -> Path:
    """Append one row to that IST day's CSV under TS_DIR/SYMBOL (header on a new file)."""
    date_ist = date_ist or row["date_ist"]
    path = ts_path_for(date_ist, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    return path


def load_day(date_ist: str, symbol: str | None = None) -> pd.DataFrame:
    """Load one IST day's time-series (empty DataFrame if none captured yet)."""
    path = ts_path_for(date_ist, symbol)
    if not path.exists():
        return pd.DataFrame(columns=FIELDS)
    return pd.read_csv(path)


def _parse_utc(ts: str) -> datetime | None:
    try:
        return datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def last_capture_utc(date_ist: str, symbol: str | None = None) -> datetime | None:
    """UTC timestamp of the most recent saved row for that IST day, or None."""
    df = load_day(date_ist, symbol)
    if df.empty or "timestamp_utc" not in df.columns:
        return None
    return _parse_utc(df["timestamp_utc"].iloc[-1])


def append_if_due(row: dict, min_gap_minutes: float = 10.0,
                  symbol: str | None = None) -> tuple[Path, bool]:
    """Append unless a row was already saved < min_gap_minutes ago (dedup guard).

    Lets independent writers (the capture loop + the dashboard auto-save) share
    one file without doubling up. Returns (path, written); on skip written=False
    and nothing is written. min_gap_minutes <= 0 disables the guard. The guard is
    per-index (it reads this symbol's day file).
    """
    date_ist = row["date_ist"]
    if min_gap_minutes > 0:
        last = last_capture_utc(date_ist, symbol)
        now = _parse_utc(row["timestamp_utc"])
        if last is not None and now is not None:
            if (now - last).total_seconds() < min_gap_minutes * 60:
                return ts_path_for(date_ist, symbol), False
    return append_row(row, symbol=symbol), True
