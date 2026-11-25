"""Load the local futures tape and the local SPAN file into the margin-stress map. No network."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import config
from analytics.liquidation_map import build_liquidation_map
from analytics.span_file import load_latest_span, margin_fracs
from analytics.structural_tape import available_futures_days, load_futures_bars

_EMPTY_READING = (
    "Margin-stress map is blank. NSE does not publish square-offs, and this panel "
    "does not borrow another index."
)


def session_gap(file_day: date, asof: date) -> int:
    """Trading sessions strictly after ``file_day`` through ``asof``."""
    from data.holidays import is_trading_day

    if file_day >= asof:
        return 0
    gap = 0
    cursor = file_day + timedelta(days=1)
    while cursor <= asof:
        if is_trading_day(cursor):
            gap += 1
        cursor += timedelta(days=1)
    return gap


def _blank(symbol: str, reason: str, reading: str, **extra) -> dict:
    payload = {
        "symbol": symbol,
        "empty": True,
        "reason": reason,
        "bins": [],
        "spot": None,
        "margin_frac_long": None,
        "margin_frac_short": None,
        "square_off_frac": config.FUTURES_SQUARE_OFF_FRAC,
        "bin_step": config.strike_step_for(symbol),
        "client_split": False,
        "participant_stale": False,
        "participant_date": None,
        "roll_window": False,
        "observed_suppressed": False,
        "reading": reading,
        "tape_day": None,
        "tape_depth": "near_month_only",
        "span_file": None,
        "span_date": None,
        "span_expiry": None,
    }
    payload.update(extra)
    return payload


def _bars_from_frame(frame) -> list[dict]:
    rows = []
    for rec in frame.to_dict("records"):
        if rec.get("oi") is None or rec.get("close") is None:
            continue
        rows.append({
            "close": float(rec["close"]),
            "high": float(rec.get("high") if rec.get("high") is not None else rec["close"]),
            "low": float(rec.get("low") if rec.get("low") is not None else rec["close"]),
            "oi": float(rec["oi"]),
        })
    return rows


def _participant(symbol: str, asof: date) -> dict:
    empty = {
        "client_long": None,
        "client_short": None,
        "total_long": None,
        "total_short": None,
        "participant_age_sessions": None,
        "participant_date": None,
    }
    if symbol == "SENSEX":
        return empty
    from app.participant_oi import latest_available

    latest = latest_available()
    if not latest:
        return empty
    day, parsed = latest
    by_name = {str(k).strip().upper(): v or {} for k, v in parsed.items()}
    client = by_name.get("CLIENT", {})
    total = by_name.get("TOTAL", {})
    file_day = datetime.strptime(day, "%Y-%m-%d").date()
    return {
        "client_long": client.get("fut_long"),
        "client_short": client.get("fut_short"),
        "total_long": total.get("fut_long"),
        "total_short": total.get("fut_short"),
        "participant_age_sessions": session_gap(file_day, asof),
        "participant_date": day,
    }


def load_liquidation_map(symbol: str, asof: date | None = None) -> dict:
    """Latest local futures session for one index, with margins from the local SPAN file."""
    sym = config.index_key(symbol)
    today = asof or date.today()
    f = config.FUTURES_SQUARE_OFF_FRAC
    if f is None:
        return _blank(sym, "square_off_unset", "Square-off fraction is unset in config.")

    try:
        loaded = load_latest_span(config.SPAN_DIR)
    except ValueError as exc:
        return _blank(sym, "span_unreadable", f"SPAN file could not be read: {exc}")
    if loaded is None:
        return _blank(
            sym,
            "span_file_missing",
            f"No NSE SPAN file in {config.SPAN_DIR.name}/. Drop the day's nsccl.YYYYMMDD.s.spn there.",
        )
    span_name, span = loaded
    contract = span["contracts"].get(sym)
    if contract is None:
        return _blank(
            sym,
            "span_index_missing",
            f"The SPAN file has no futures for {sym}.",
            span_file=span_name,
            span_date=span["file_date"],
        )
    try:
        r_long, r_short = margin_fracs(contract, config.FUTURES_EXPOSURE_FRAC)
    except ValueError as exc:
        return _blank(sym, "margin_frac_unset", str(exc), span_file=span_name, span_date=span["file_date"])

    days = available_futures_days(sym)
    if not days:
        return _blank(
            sym,
            "no_futures_tape",
            _EMPTY_READING + " No futures tape is stored for this index.",
            margin_frac_long=r_long,
            margin_frac_short=r_short,
            span_file=span_name,
            span_date=span["file_date"],
            span_expiry=contract["expiry"],
        )
    day = days[-1]
    if day != span["file_date"]:
        # Margin fractions are for the SPAN file's date. Bands built from another session's
        # open interest would sit at the wrong prices, so hide them rather than draw them.
        return _blank(
            sym,
            "tape_span_mismatch",
            f"Latest futures session {day} does not match the SPAN file date {span['file_date']}. "
            "Bands hidden until both are the same session.",
            margin_frac_long=r_long,
            margin_frac_short=r_short,
            tape_day=day,
            span_file=span_name,
            span_date=span["file_date"],
            span_expiry=contract["expiry"],
        )
    # Concatenate every stored session in order. Open interest is continuous across
    # sessions, so an OI rise on one day's first print seeds a band and a later fall closes
    # it FIFO. Only the window's first print is unplaced (its entry is unknown).
    bars: list[dict] = []
    used: list[str] = []
    for d in days:
        frame = load_futures_bars(sym, d)
        if frame is None:
            continue
        rows = _bars_from_frame(frame)
        if rows:
            bars.extend(rows)
            used.append(d)
    if not bars:
        return _blank(
            sym,
            "oi_missing",
            _EMPTY_READING + " No stored session has open interest.",
            tape_day=day,
        )
    part = _participant(sym, today)
    spot = bars[-1]["close"]
    try:
        payload = build_liquidation_map(
            bars,
            margin_frac_long=r_long,
            margin_frac_short=r_short,
            square_off_frac=f,
            bin_step=config.strike_step_for(sym),
            symbol=sym,
            spot=spot,
            **part,
        )
    except ValueError as exc:
        return _blank(sym, "margin_frac_unset", str(exc), tape_day=day)
    payload["tape_day"] = used[-1]
    payload["tape_first_day"] = used[0]
    payload["tape_depth"] = "all_stored_sessions_near_month"
    payload["sessions_used"] = len(used)
    payload["sessions_stored"] = len(days)
    payload["span_file"] = span_name
    payload["span_date"] = span["file_date"]
    payload["span_expiry"] = contract["expiry"]
    payload["tape_stale"] = day < span["file_date"]
    payload["span_price"] = contract["price"]
    payload["span_frac_long"] = round(contract["span_frac_long"], 6)
    payload["span_frac_short"] = round(contract["span_frac_short"], 6)
    return payload
