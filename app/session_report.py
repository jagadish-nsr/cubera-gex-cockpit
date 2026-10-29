"""Assemble one post-close session report from the journals already on disk.

The page is a reading of what was recorded. A missing store stays missing.
A stored range that does not contain the settlement print is not shown as the
day's range.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RUPEE = re.compile(r"₹\s*([0-9][0-9,]*(?:\.\d+)?)")

CAVEAT = (
    "This is a reading of the recorded book and the recorded outcomes. "
    "It is not a price forecast. You confirm every decision."
)


def reports_dir(root: Path) -> Path:
    return Path(root) / "data" / "journal" / "reports"


def build_session_report(session_date: str, root: Path | None = None) -> dict[str, Any]:
    """Build the JSON document for one session date. No network."""
    if not _DATE.match(session_date):
        raise ValueError(f"session date must be YYYY-MM-DD, got {session_date!r}")
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    day = datetime.strptime(session_date, "%Y-%m-%d")

    cash = _timeseries(root, "NIFTY", session_date)
    for symbol in ("BANKNIFTY", "SENSEX"):
        other = _timeseries(root, symbol, session_date)
        if other["present"]:
            cash.setdefault("others", []).append(other)
    indices = _index_prints(root, session_date, cash)
    recommendations = _recommendations(root, session_date)
    commentary = _commentary(root, session_date, cash)
    sentinel = _sentinel(root, session_date)
    stalls = _stall_count(root, session_date)
    prior = _prior_sessions(root, session_date)
    notes = _notes(cash, indices, recommendations, commentary, sentinel, stalls)

    scorecard = _scorecard(cash, indices, recommendations, commentary, sentinel)
    summary = _summary(session_date, cash, recommendations, commentary, sentinel)
    return {
        "session_date": session_date,
        "title": f"Session report — {day.strftime('%A')}, {day.day} {day.strftime('%b %Y')}",
        "generated_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "summary": summary,
        "scorecard": scorecard,
        "cash_path": cash,
        "indices": indices,
        "recommendations": recommendations,
        "commentary": commentary,
        "sentinel": sentinel,
        "prior": prior,
        "notes": notes,
        "caveat": CAVEAT,
    }


def write_session_report(session_date: str, root: Path | None = None) -> dict[str, Any]:
    """Write the JSON and HTML for one date. Returns the document."""
    from app.session_report_html import render_session_report

    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    doc = build_session_report(session_date, root)
    out = reports_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{session_date}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    (out / f"{session_date}.html").write_text(render_session_report(doc), encoding="utf-8")
    return doc


def _summary(session_date: str, cash: dict, recommendations: dict, commentary: dict, sentinel: dict) -> str:
    parts = [session_date]
    if cash.get("present"):
        parts.append(
            f"Nifty 15-minute prints { _px(cash['open_spot']) } to { _px(cash['close_spot']) } "
            f"({_pts(cash['sample_change_pts'])} pts on the samples)"
        )
    else:
        parts.append("Nifty 15-minute series is not on disk")
    parts.append(f"{recommendations['total']} recommendations")
    parts.append(f"{commentary['entries']} commentary entries")
    if sentinel["signals"]:
        parts.append(f"{sentinel['signals']} sentinel signals, {sentinel['hit_rate_pct']}% target hits")
    else:
        parts.append("no sentinel signals for this date in sentinel.db")
    return ". ".join(parts) + "."


def _scorecard(cash, indices, recommendations, commentary, sentinel) -> list[dict]:
    rows = []
    if cash.get("present"):
        rows.append({
            "label": "Nifty cash samples",
            "value": f"{_px(cash['open_spot'])} → {_px(cash['close_spot'])}",
            "detail": (
                f"{_pts(cash['sample_change_pts'])} pts on {cash['intervals']} prints, "
                f"{cash['first_time']}–{cash['last_time']} IST"
            ),
        })
    nifty_settle = next((i for i in indices if i["symbol"] == "NIFTY" and i.get("settled")), None)
    if nifty_settle and nifty_settle.get("net_change_pts") is not None:
        rows.append({
            "label": "Nifty settlement",
            "value": f"{_px(nifty_settle['settlement_spot'])} ({_pts(nifty_settle['net_change_pts'])} pts)",
            "detail": f"{nifty_settle['net_change_pct']:+.2f}% versus the stored previous close",
        })
    rows.append({
        "label": "Recommendations",
        "value": str(recommendations["total"]),
        "detail": recommendations["status_line"] or "none recorded",
    })
    rows.append({
        "label": "Commentary",
        "value": str(commentary["entries"]),
        "detail": commentary["span"] or "no entries",
    })
    if sentinel["signals"]:
        rows.append({
            "label": "Sentinel target hits",
            "value": f"{sentinel['target_hits']} / {sentinel['signals']}",
            "detail": f"{sentinel['hit_rate_pct']}% · MFE {_pts(sentinel['avg_mfe'])} · MAE {_pts(sentinel['avg_mae'])}",
        })
    else:
        rows.append({
            "label": "Sentinel",
            "value": "0 signals",
            "detail": "no rows dated this session in data/sentinel.db",
        })
    return rows


def _notes(cash, indices, recommendations, commentary, sentinel, stalls) -> list[str]:
    notes: list[str] = []
    if cash.get("present"):
        if cash["stale_rows"]:
            notes.append(f"{cash['stale_rows']} Nifty timeseries rows are marked stale.")
        else:
            notes.append(
                f"Nifty cash series has {cash['intervals']} prints from {cash['first_time']} to "
                f"{cash['last_time']} IST and none are marked stale. "
                "High and low are the extremes of those prints, not the tick range."
            )
        if cash.get("regime_flip_time"):
            notes.append(
                f"The stored book sign moved to {cash['regime_flip_to']} at {cash['regime_flip_time']} IST "
                f"(spot {_px(cash['regime_flip_spot'])}). "
                "POSITIVE is call-heavy. NEGATIVE is put-heavy. The sign is not a direction call."
            )
    else:
        notes.append("Nifty 15-minute timeseries file is not on disk for this date.")
    for row in indices:
        if row.get("range_note"):
            notes.append(row["range_note"])
        if row.get("snapshot_role") == "pre-open":
            notes.append(
                f"{row['symbol']} snapshot is timed {row['snapshot_ist']} IST, before the open. "
                "It is not the session close."
            )
    if recommendations["total"] and recommendations.get("last_at"):
        symbols = ", ".join(recommendations["symbols"]) or "none"
        notes.append(
            f"Recommendations run from {recommendations['first_at']} to {recommendations['last_at']}. "
            f"Symbols recorded: {symbols}."
        )
    if commentary["entries"] and cash.get("present") and commentary.get("last_hm"):
        if commentary["last_hm"] < cash["last_time"]:
            notes.append(
                f"The last commentary entry is {commentary['last_hm']} IST. "
                f"The cash series continues to {cash['last_time']} IST."
            )
    for extra in commentary.get("headline_notes") or []:
        notes.append(extra)
    if not sentinel["signals"]:
        last = sentinel.get("last_signal_date")
        when = f" The latest signal date in that file is {last}." if last else ""
        notes.append(f"Sentinel outcomes for this date are not in data/sentinel.db.{when}")
    if sentinel.get("test_cvd"):
        notes.append(
            "A closing-CVD row exists for this date on a TEST instrument. "
            "It is not counted as Nifty, Bank Nifty, or Sensex."
        )
    if stalls is None:
        notes.append("The web log was not scanned for event-loop stalls.")
    elif stalls:
        notes.append(f"The web log contains {stalls} 'event loop stalled' lines dated this session.")
    else:
        notes.append("The web log has no 'event loop stalled' line dated this session.")
    return notes


def _timeseries(root: Path, symbol: str, session_date: str) -> dict[str, Any]:
    path = root / "data" / "journal" / "timeseries" / symbol / f"{session_date}.csv"
    empty: dict[str, Any] = {"present": False, "symbol": symbol, "path": str(path)}
    if not path.is_file():
        return empty
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    parsed = []
    for row in rows:
        try:
            spot = float(row["spot"])
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append((row, spot))
    if not parsed:
        empty["present"] = False
        empty["intervals"] = 0
        return empty
    spots = [s for _, s in parsed]
    first, last = parsed[0][0], parsed[-1][0]
    stale = sum(1 for row, _ in parsed if str(row.get("stale", "")).lower() == "true")
    flip_time = flip_to = flip_spot = None
    open_regime = str(first.get("regime") or "")
    for row, spot in parsed:
        regime = str(row.get("regime") or "")
        if open_regime and regime and regime != open_regime:
            flip_time = row.get("time_ist")
            flip_to = regime
            flip_spot = spot
            break
    walls = set()
    for row, _ in parsed:
        for key in ("call_wall", "put_wall"):
            try:
                walls.add(float(row[key]))
            except (KeyError, TypeError, ValueError):
                pass
    return {
        "present": True,
        "symbol": symbol,
        "intervals": len(parsed),
        "stale_rows": stale,
        "first_time": first.get("time_ist") or "",
        "last_time": last.get("time_ist") or "",
        "open_spot": spots[0],
        "close_spot": spots[-1],
        "sample_high": max(spots),
        "sample_low": min(spots),
        "sample_change_pts": round(spots[-1] - spots[0], 2),
        "open_regime": open_regime,
        "close_regime": str(last.get("regime") or ""),
        "open_mode": str(first.get("mode_label") or ""),
        "close_mode": str(last.get("mode_label") or ""),
        "call_wall_open": _num(first.get("call_wall")),
        "call_wall_close": _num(last.get("call_wall")),
        "put_wall_open": _num(first.get("put_wall")),
        "put_wall_close": _num(last.get("put_wall")),
        "flip_open": _num(first.get("flip")),
        "flip_close": _num(last.get("flip")),
        "magnet_close": _num(last.get("magnet")),
        "max_pain_close": _num(last.get("max_pain")),
        "net_gex_close": _num(last.get("net_gex")),
        "vix_open": _num(first.get("india_vix")),
        "vix_close": _num(last.get("india_vix")),
        "vix_pct_chg_close": _num(last.get("vix_pct_chg")),
        "pcr_close": _num(last.get("pcr_oi")),
        "regime_flip_time": flip_time,
        "regime_flip_to": flip_to,
        "regime_flip_spot": flip_spot,
        "wall_strikes": sorted(walls),
        "prints": [
            {
                "time": row.get("time_ist") or "",
                "spot": spot,
                "regime": row.get("regime") or "",
                "mode": row.get("mode_label") or "",
                "call_wall": _num(row.get("call_wall")),
                "put_wall": _num(row.get("put_wall")),
                "flip": _num(row.get("flip")),
            }
            for row, spot in parsed
        ],
    }


def _index_prints(root: Path, session_date: str, cash: dict) -> list[dict]:
    snap_by = {r["symbol"]: r for r in _read_rows(
        root / "data" / "journal" / "market_snapshot.db",
        "SELECT * FROM session_snapshots WHERE as_of LIKE ?",
        (session_date + "%",),
    )}
    auctions = _read_rows(
        root / "data" / "journal" / "market_snapshot.db",
        "SELECT * FROM market_auctions_history WHERE trade_date = ?",
        (session_date,),
    )
    auction_by = {r["symbol"]: r for r in auctions}
    symbols = []
    for symbol in ("NIFTY", "BANKNIFTY", "SENSEX"):
        if symbol in snap_by or symbol in auction_by or (symbol == "NIFTY" and cash.get("present")):
            symbols.append(symbol)
    out = []
    for symbol in symbols:
        snap = snap_by.get(symbol) or {}
        auction = auction_by.get(symbol) or {}
        spot = _num(snap.get("spot"))
        hi = _num(snap.get("high"))
        lo = _num(snap.get("low"))
        as_of = snap.get("as_of") or ""
        ist_label, role = _snapshot_role(as_of, session_date)
        range_used = spot is not None and hi is not None and lo is not None and min(lo, hi) - 0.05 <= spot <= max(lo, hi) + 0.05
        settlement = _num(auction.get("settlement_spot"))
        settled = bool(auction.get("post_market_settled")) and settlement is not None and settlement > 0
        stored_range = _parse_range(auction.get("day_range"))
        auction_range_used = False
        if stored_range and settlement is not None:
            lo_r, hi_r = stored_range
            auction_range_used = lo_r - 0.05 <= settlement <= hi_r + 0.05
        range_note = ""
        if auction.get("day_range") and settled and not auction_range_used:
            range_note = (
                f"{symbol} stored day range {auction.get('day_range')} does not contain the "
                f"settlement print {_px(settlement)}. It is not shown as this session's range."
            )
        elif snap and spot is not None and not range_used and role != "pre-open":
            range_note = (
                f"{symbol} snapshot open/high/low does not contain the snapshot spot {_px(spot)}. "
                "Those three fields are not shown as this session's range."
            )
        net_gex = _num(snap.get("net_gex"))
        if symbol == "NIFTY" and cash.get("present"):
            net_gex = cash.get("net_gex_close")
        out.append({
            "symbol": symbol,
            "snapshot_role": role,
            "snapshot_ist": ist_label,
            "spot": spot,
            "prev_close_snapshot": _num(snap.get("prev_close")),
            "range_used": range_used,
            "open": _num(snap.get("open")) if range_used else None,
            "high": hi if range_used else None,
            "low": lo if range_used else None,
            "call_wall": _num(snap.get("call_wall")) if symbol != "NIFTY" or not cash.get("present") else cash.get("call_wall_close"),
            "put_wall": _num(snap.get("put_wall")) if symbol != "NIFTY" or not cash.get("present") else cash.get("put_wall_close"),
            "gamma_flip": _num(snap.get("gamma_flip")) if symbol != "NIFTY" or not cash.get("present") else cash.get("flip_close"),
            "max_pain": _num(snap.get("max_pain")) if symbol != "NIFTY" or not cash.get("present") else cash.get("max_pain_close"),
            "net_gex": net_gex,
            "book": _book_label(net_gex if symbol != "NIFTY" else cash.get("net_gex_close"), cash.get("close_regime") if symbol == "NIFTY" else None),
            "settled": settled,
            "settlement_spot": settlement if settled else None,
            "prev_close": _num(auction.get("prev_close")) if settled else None,
            "net_change_pts": _num(auction.get("net_change_pts")) if settled else None,
            "net_change_pct": _num(auction.get("net_change_pct")) if settled else None,
            "auction_range_used": auction_range_used,
            "discovered_spot": _num(auction.get("discovered_spot")),
            "gap_pts": _num(auction.get("gap_pts")),
            "gap_pct": _num(auction.get("gap_pct")),
            "range_note": range_note,
            "cash_file": symbol == "NIFTY" and bool(cash.get("present")),
        })
    return out


def _recommendations(root: Path, session_date: str) -> dict[str, Any]:
    db = root / "data" / "journal" / "recommendations.db"
    rows = _read_rows(
        db,
        """
        SELECT symbol, side, status, COUNT(*) AS n
        FROM recommendation_records
        WHERE substr(triggered_at, 1, 10) = ?
        GROUP BY symbol, side, status
        ORDER BY symbol, side, status
        """,
        (session_date,),
    )
    span = _read_rows(
        db,
        """
        SELECT MIN(triggered_at) AS first_at, MAX(triggered_at) AS last_at, COUNT(*) AS n
        FROM recommendation_records
        WHERE substr(triggered_at, 1, 10) = ?
        """,
        (session_date,),
    )
    symbols = _read_rows(
        db,
        """
        SELECT DISTINCT symbol FROM recommendation_records
        WHERE substr(triggered_at, 1, 10) = ?
        ORDER BY symbol
        """,
        (session_date,),
    )
    events = _read_rows(
        db,
        """
        SELECT event_type, COUNT(*) AS n
        FROM recommendation_events
        WHERE substr(event_ts, 1, 10) = ?
        GROUP BY event_type
        ORDER BY n DESC
        """,
        (session_date,),
    )
    total = int((span[0]["n"] if span else 0) or 0)
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + int(row["n"])
    status_line = ", ".join(f"{n} {status}" for status, n in sorted(by_status.items()))
    return {
        "total": total,
        "first_at": (span[0]["first_at"] if span else None) or "",
        "last_at": (span[0]["last_at"] if span else None) or "",
        "symbols": [r["symbol"] for r in symbols],
        "by_side_status": rows,
        "by_status": by_status,
        "status_line": status_line,
        "events": events,
    }


def _commentary(root: Path, session_date: str, cash: dict) -> dict[str, Any]:
    db = root / "data" / "journal" / "commentary.db"
    entries = _read_rows(
        db,
        """
        SELECT ts, symbol, phase, headline
        FROM commentary_entries
        WHERE substr(ts, 1, 10) = ?
        ORDER BY ts
        """,
        (session_date,),
    )
    bites = _read_rows(
        db,
        """
        SELECT state, COUNT(*) AS n
        FROM commentary_bites
        WHERE substr(ts, 1, 10) = ?
        GROUP BY state
        """,
        (session_date,),
    )
    event_n = _scalar(
        db,
        "SELECT COUNT(*) FROM commentary_events WHERE substr(ts, 1, 10) = ?",
        (session_date,),
    )
    headlines = []
    for row in entries:
        ts = str(row.get("ts") or "")
        hm = ts[11:16] if len(ts) >= 16 else ""
        headlines.append({
            "time": hm,
            "symbol": row.get("symbol") or "",
            "phase": row.get("phase") or "",
            "headline": row.get("headline") or "",
        })
    walls = set(cash.get("wall_strikes") or [])
    wall_rounds = {int(round(w)) for w in walls}
    headline_notes = []
    for item in headlines:
        if "migrat" not in item["headline"].lower():
            continue
        named = []
        for raw in _RUPEE.findall(item["headline"]):
            try:
                named.append(int(round(float(raw.replace(",", "")))))
            except ValueError:
                continue
        missing = [n for n in named if n not in wall_rounds]
        if missing and wall_rounds:
            shown = ", ".join(f"₹{n:,.0f}" for n in missing)
            have = ", ".join(f"₹{n:,.0f}" for n in sorted(wall_rounds))
            headline_notes.append(
                f"Commentary at {item['time']} IST says the call wall migrated through {shown}. "
                f"The 15-minute chain's call and put walls on this date were {have}."
            )
    last_hm = headlines[-1]["time"] if headlines else ""
    first_hm = headlines[0]["time"] if headlines else ""
    span = f"{first_hm}–{last_hm} IST" if headlines else ""
    return {
        "entries": len(headlines),
        "events": int(event_n or 0),
        "bites": bites,
        "headlines": headlines,
        "headline_notes": headline_notes,
        "last_hm": last_hm,
        "span": span,
    }


def _sentinel(root: Path, session_date: str) -> dict[str, Any]:
    db = root / "data" / "sentinel.db"
    signals = _read_rows(
        db,
        """
        SELECT s.instrument_id AS instrument_id,
               s.type AS type,
               s.bias AS bias,
               COUNT(*) AS n,
               SUM(CASE WHEN o.target_hit = 1 THEN 1 ELSE 0 END) AS hits,
               AVG(o.mfe_pts) AS avg_mfe,
               AVG(o.mae_pts) AS avg_mae
        FROM sentinel_signals s
        LEFT JOIN signal_outcomes o ON o.event_id = s.event_id
        WHERE substr(s.bar_start_ts, 1, 10) = ?
        GROUP BY s.instrument_id, s.type, s.bias
        ORDER BY n DESC
        """,
        (session_date,),
    )
    totals = _read_rows(
        db,
        """
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN o.target_hit = 1 THEN 1 ELSE 0 END) AS hits,
               AVG(o.mfe_pts) AS avg_mfe,
               AVG(o.mae_pts) AS avg_mae
        FROM sentinel_signals s
        LEFT JOIN signal_outcomes o ON o.event_id = s.event_id
        WHERE substr(s.bar_start_ts, 1, 10) = ?
        """,
        (session_date,),
    )
    bars = _scalar(
        db,
        """
        SELECT COUNT(*) FROM sentinel_bars
        WHERE timeframe = '1m' AND substr(start_ts, 1, 10) = ?
        """,
        (session_date,),
    )
    last = _read_rows(
        db,
        "SELECT substr(bar_start_ts, 1, 10) AS d FROM sentinel_signals ORDER BY bar_start_ts DESC LIMIT 1",
    )
    cvd = _read_rows(
        db,
        """
        SELECT instrument_id, closing_cvd, closing_price
        FROM sentinel_daily_cvd
        WHERE trade_date = ?
        """,
        (session_date,),
    )
    n = int((totals[0]["n"] if totals else 0) or 0)
    hits = int((totals[0]["hits"] if totals else 0) or 0)
    mfe = float((totals[0]["avg_mfe"] if totals and totals[0]["avg_mfe"] is not None else 0) or 0)
    mae = float((totals[0]["avg_mae"] if totals and totals[0]["avg_mae"] is not None else 0) or 0)
    live_cvd = [r for r in cvd if "TEST" not in str(r.get("instrument_id") or "").upper()]
    return {
        "signals": n,
        "target_hits": hits,
        "hit_rate_pct": round(hits / n * 100.0, 1) if n else 0.0,
        "avg_mfe": round(mfe, 2),
        "avg_mae": round(mae, 2),
        "edge_ratio": round(mfe / mae, 2) if mae > 0.001 else None,
        "bars_1m": int(bars or 0),
        "by_type": signals,
        "last_signal_date": (last[0]["d"] if last else None),
        "closing_cvd": live_cvd,
        "test_cvd": any("TEST" in str(r.get("instrument_id") or "").upper() for r in cvd),
    }


def _prior_sessions(root: Path, session_date: str) -> list[dict]:
    folder = root / "data" / "journal" / "timeseries" / "NIFTY"
    if not folder.is_dir():
        return []
    dates = sorted(p.stem for p in folder.glob("*.csv") if _DATE.match(p.stem) and p.stem < session_date)
    rows = []
    for day in dates[-3:]:
        series = _timeseries(root, "NIFTY", day)
        rec_n = _scalar(
            root / "data" / "journal" / "recommendations.db",
            "SELECT COUNT(*) FROM recommendation_records WHERE substr(triggered_at, 1, 10) = ?",
            (day,),
        )
        comm_n = _scalar(
            root / "data" / "journal" / "commentary.db",
            "SELECT COUNT(*) FROM commentary_entries WHERE substr(ts, 1, 10) = ?",
            (day,),
        )
        sig_n = _scalar(
            root / "data" / "sentinel.db",
            "SELECT COUNT(*) FROM sentinel_signals WHERE substr(bar_start_ts, 1, 10) = ?",
            (day,),
        )
        rows.append({
            "session_date": day,
            "nifty_open": series.get("open_spot"),
            "nifty_close": series.get("close_spot"),
            "sample_change_pts": series.get("sample_change_pts"),
            "intervals": series.get("intervals") or 0,
            "recommendations": int(rec_n or 0),
            "commentary": int(comm_n or 0),
            "sentinel_signals": int(sig_n or 0),
        })
    return rows


def _stall_count(root: Path, session_date: str) -> int | None:
    path = root / "logs" / "web.jsonl"
    if not path.is_file():
        return None
    count = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if session_date in line and "event loop stalled" in line:
                count += 1
    return count


def _snapshot_role(as_of: str, session_date: str) -> tuple[str, str]:
    if not as_of:
        return "", "absent"
    try:
        dt = datetime.fromisoformat(as_of)
    except ValueError:
        return as_of, "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(IST)
    label = local.strftime("%H:%M")
    if local.strftime("%Y-%m-%d") != session_date:
        return label, "other-date"
    minutes = local.hour * 60 + local.minute
    if minutes < 9 * 60 + 15:
        return label, "pre-open"
    if minutes >= 15 * 60 + 15:
        return label, "after-close"
    return label, "in-session"


def _parse_range(text: str | None) -> tuple[float, float] | None:
    if not text or "/" not in str(text):
        return None
    nums = []
    for part in str(text).split("/"):
        cleaned = part.replace("₹", "").replace(",", "").strip()
        try:
            nums.append(float(cleaned))
        except ValueError:
            return None
    if len(nums) != 2:
        return None
    return min(nums), max(nums)


def _book_label(net_gex: float | None, regime: str | None) -> str:
    if regime:
        if regime.upper() == "POSITIVE":
            return "call-heavy"
        if regime.upper() == "NEGATIVE":
            return "put-heavy"
    if net_gex is None:
        return ""
    if net_gex > 0:
        return "call-heavy"
    if net_gex < 0:
        return "put-heavy"
    return "balanced"


def _read_rows(db: Path, sql: str, args: tuple = ()) -> list[dict]:
    if not Path(db).is_file() or Path(db).stat().st_size == 0:
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, args)]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _scalar(db: Path, sql: str, args: tuple = ()) -> int | None:
    rows = _read_rows(db, sql, args)
    if not rows:
        return None
    return list(rows[0].values())[0]


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _px(value: float | None) -> str:
    if value is None:
        return "—"
    return f"₹{value:,.2f}"


def _pts(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+,.2f}"
