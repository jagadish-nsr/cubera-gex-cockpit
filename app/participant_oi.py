"""NSE participant-wise OI — the DIRECT big-player positioning read, sign-free.

Client / DII / FII / Pro long-vs-short in index F&O, from NSE's clearing-corp EOD
file. Unlike GEX (which infers positioning through the option chain and sits under
the unvalidated dealer-*gamma*-sign landmine), this says WHO is actually positioned
— so it's the cleanest clue to who writes index options. See docs/TODO.md for the
verified endpoint/format and memory/positioning-footprint-suite.md for the thesis.

Data is EOD (T+0 after close) and MARKET-WIDE (not split Nifty-vs-BankNifty), so the
journal is flat: one CSV per trading day, one row per participant. This module holds
the pure signal logic + journal I/O + the best-effort fetch/cache orchestration
(shared by the dashboard and the daily capture script). Decision-support only.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
# Flat, market-wide (not per-index): data/journal/participant_oi/YYYY-MM-DD.csv.
# The whole data/journal/ tree is gitignored (personal data).
PARTICIPANT_DIR = ROOT / "data" / "journal" / "participant_oi"

# One row per participant per day. Keep in step with save/load below.
PARTICIPANT_FIELDS = [
    "date_ist", "participant",
    "fut_long", "fut_short",
    "opt_call_long", "opt_put_long", "opt_call_short", "opt_put_short",
    "fut_stk_long", "fut_stk_short",
]
_NUM_FIELDS = PARTICIPANT_FIELDS[2:]


def _path_for(date_ist: str) -> Path:
    # Resolve PARTICIPANT_DIR at CALL time (tests monkeypatch it).
    return PARTICIPANT_DIR / f"{date_ist}.csv"


def save_participant_day(date_ist: str, parsed: dict) -> Path | None:
    """Write one day's parsed participant OI (``{participant: {fields}}``) to the
    journal, one row per participant. Idempotent: if the file already exists it's a
    no-op (the EOD file is final, so re-fetching the same day must not double-write).
    Returns the path (existing or written), or None if there's nothing to save."""
    if not parsed:
        return None
    path = _path_for(date_ist)
    if path.exists():
        return path                       # already captured — final EOD file
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PARTICIPANT_FIELDS)
        w.writeheader()
        for who, vals in parsed.items():
            row = {"date_ist": date_ist, "participant": who}
            row.update({k: vals.get(k) for k in _NUM_FIELDS})
            w.writerow(row)
    return path


def load_participant_day(date_ist: str) -> dict | None:
    """Load one day's participant OI back into ``{participant: {fields}}`` (the same
    shape parse_participant_oi produces), or None if not captured yet."""
    path = _path_for(date_ist)
    if not path.exists():
        return None
    out: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            who = r.get("participant")
            if not who:
                continue
            vals = {}
            for k in _NUM_FIELDS:
                v = r.get(k)
                try:
                    vals[k] = int(float(v)) if v not in (None, "") else None
                except (ValueError, TypeError):
                    vals[k] = None
            out[who] = vals
    return out or None


def available_days() -> list[str]:
    """Sorted IST dates (YYYY-MM-DD) with a captured participant-OI file."""
    if not PARTICIPANT_DIR.exists():
        return []
    return sorted(p.stem for p in PARTICIPANT_DIR.glob("*.csv"))


def latest_available() -> tuple[str, dict] | None:
    """The most recent captured day as ``(date_ist, parsed)``, or None if empty."""
    days = available_days()
    if not days:
        return None
    parsed = load_participant_day(days[-1])
    return (days[-1], parsed) if parsed else None


def derive_signals(parsed: dict) -> dict | None:
    """Turn parsed participant OI into positioning signals (sign-free).

    Per participant: ``net_fut = fut_long - fut_short`` (index-future direction) and
    ``net_opt_writing = (call_short+put_short) - (call_long+put_long)`` (>0 = net
    option WRITER). The dominant writer is a positioning hint only. It does not
    select a second GEX sign model. The engine's signed total stays call magnitude
    minus put magnitude.
    """
    if not parsed:
        return None
    per = {}
    for who, v in parsed.items():
        fl, fs = v.get("fut_long"), v.get("fut_short")
        cl, pl = v.get("opt_call_long"), v.get("opt_put_long")
        cs, ps = v.get("opt_call_short"), v.get("opt_put_short")
        net_fut = (fl - fs) if fl is not None and fs is not None else None
        opt_short = (cs or 0) + (ps or 0)
        opt_long = (cl or 0) + (pl or 0)
        net_writing = opt_short - opt_long if (cs is not None or ps is not None) else None
        per[who] = {"net_fut": net_fut, "net_opt_writing": net_writing}

    # Dealer-sign hint: dominant net option writer among the non-DII participants
    # (Client/Pro/FII are the index-option desks; DII barely writes index options).
    writers = {w: per[w]["net_opt_writing"] for w in ("Client", "Pro", "FII")
               if per.get(w) and per[w]["net_opt_writing"] is not None}
    top_writer = max(writers, key=writers.get) if writers else None
    hint = None
    if top_writer:
        hint = f"{top_writer} is the dominant index-option writer."
    return {"per_participant": per, "top_option_writer": top_writer,
            "dealer_sign_hint": hint,
            "fii_net_fut": (per.get("FII") or {}).get("net_fut")}


def _net_calls_puts(v: dict) -> tuple[int | None, int | None]:
    """(net_calls, net_puts) = long − short, per side. Net short puts = bullish
    (put writing); net short calls = bearish (call writing)."""
    cl, cs = v.get("opt_call_long"), v.get("opt_call_short")
    pl, ps = v.get("opt_put_long"), v.get("opt_put_short")
    nc = cl - cs if cl is not None and cs is not None else None
    npu = pl - ps if pl is not None and ps is not None else None
    return nc, npu


def participant_series(who: str = "FII", n: int = 5, days: list[str] | None = None) -> list[dict]:
    """Per-day positioning metrics for one participant over the last ``n`` captured
    days (chronological). Each row: date, net_fut (fut long−short), net_writing
    (opt short−long), net_calls, net_puts. Skips days missing that participant.
    Reads the flat market-wide journal (participant OI is not per-index)."""
    days = days if days is not None else available_days()
    out: list[dict] = []
    for d in days[-n:] if n else days:
        parsed = load_participant_day(d)
        if not parsed or who not in parsed:
            continue
        v = parsed[who]
        sig = (derive_signals(parsed) or {}).get("per_participant", {}).get(who, {})
        nc, npu = _net_calls_puts(v)
        out.append({"date": d, "net_fut": sig.get("net_fut"),
                    "net_writing": sig.get("net_opt_writing"),
                    "net_calls": nc, "net_puts": npu})
    return out


def series_drift(values, tol_frac: float = 0.10) -> dict:
    """Slope-based drift for a SIGNED series that may cross zero (FII net-futures,
    option-writing). Unlike classify_trend (whose tolerance is level-relative and
    degenerates near zero), the "meaningful move" band here scales with the series'
    typical MAGNITUDE (mean |value|). Returns trend (rising/falling/flat), the fitted
    drift over the window, first/last, and n. Rising = getting more positive."""
    vals = [float(v) for v in values if v is not None and np.isfinite(v)]
    n = len(vals)
    if n == 0:
        return {"trend": "unknown", "n": 0}
    if n < 2:
        return {"trend": "flat", "drift": 0.0, "first": vals[0], "last": vals[0], "n": n}
    slope = float(np.polyfit(np.arange(n), vals, 1)[0])
    drift = slope * (n - 1)
    scale = float(np.mean(np.abs(vals))) or 1.0
    tol = max(scale * tol_frac, 1.0)
    trend = "rising" if drift > tol else "falling" if drift < -tol else "flat"
    return {"trend": trend, "drift": drift, "first": vals[0], "last": vals[-1], "n": n}


def _ist_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def capture_latest(today_ist: str | None = None, fetch_fn=None, parse_fn=None,
                   max_back: int = 4) -> tuple[str, dict] | None:
    """Best-effort: fetch the newest available participant-OI file and cache it.

    Tries ``today_ist`` first, then walks back up to ``max_back`` calendar days (the
    EOD file may not be published yet, or the day is a weekend/holiday). On the first
    day that parses, caches it (idempotent) and returns ``(date_ist, parsed)``. If a
    day is already cached, returns it without re-fetching. Returns None if nothing is
    reachable — callers fall back to ``latest_available()``. fetch_fn/parse_fn are
    injectable for tests (default to the live data.nse functions)."""
    if fetch_fn is None or parse_fn is None:
        from data.nse import fetch_participant_oi, parse_participant_oi
        fetch_fn = fetch_fn or fetch_participant_oi
        parse_fn = parse_fn or parse_participant_oi

    today_ist = today_ist or _ist_today()
    day = datetime.strptime(today_ist, "%Y-%m-%d").date()
    for _ in range(max_back + 1):
        date_ist = day.strftime("%Y-%m-%d")
        cached = load_participant_day(date_ist)
        if cached:
            return (date_ist, cached)
        text = fetch_fn(day)
        parsed = parse_fn(text) if text else None
        if parsed:
            save_participant_day(date_ist, parsed)
            return (date_ist, parsed)
        day = day - timedelta(days=1)
    return None


def backfill(n_days: int = 10, today_ist: str | None = None,
             fetch_fn=None, parse_fn=None) -> list[str]:
    """Fetch + cache every available participant-OI file over the last ``n_days``
    calendar days (today back). Non-trading days (weekends/holidays) simply 404 and
    are skipped; already-cached days are left as-is (idempotent). Returns the list of
    dates newly captured this run, oldest first. Unlike capture_latest (newest only),
    this fills the whole recent history so the multi-day FII drift has data."""
    if fetch_fn is None or parse_fn is None:
        from data.nse import fetch_participant_oi, parse_participant_oi
        fetch_fn = fetch_fn or fetch_participant_oi
        parse_fn = parse_fn or parse_participant_oi

    today_ist = today_ist or _ist_today()
    day = datetime.strptime(today_ist, "%Y-%m-%d").date()
    captured: list[str] = []
    for _ in range(n_days):
        date_ist = day.strftime("%Y-%m-%d")
        if load_participant_day(date_ist) is None:
            text = fetch_fn(day)
            parsed = parse_fn(text) if text else None
            if parsed:
                save_participant_day(date_ist, parsed)
                captured.append(date_ist)
        day = day - timedelta(days=1)
    return sorted(captured)
