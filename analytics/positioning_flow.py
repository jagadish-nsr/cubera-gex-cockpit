"""Two clocks for index-futures positioning.

Overnight clock: NSE participant file, FII index-futures drift, plus the
per-index aggregate futures quadrant. The file is all NSE index futures
together. It does not cover Sensex. A net-short futures book without short
calls or long puts stays a cash hedge.

Live clock: the same quadrant on today's futures journal, plus session CVD
and buy-versus-sell burst notional. The tape has no participant name.

An alert fires only when the overnight label and the live long/short build-up
match, the bursts and CVD confirm, and the file date is the previous NSE
session. Recommendation scores are not an input and not an output.
"""
from __future__ import annotations

from app.futures_buildup import classify_futures_buildup
from app.participant_oi import series_drift

MIN_SESSIONS = 5
LIVE_PRICE_TOL_FRAC = 0.0004
LIVE_OI_TOL_FRAC = 0.0005
TRAIL_MINUTES = 30

_PAIR = {
    "accumulation": "long build-up",
    "distribution": "short build-up",
}


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _contracts(value) -> str:
    num = _num(value)
    if num is None:
        return "unavailable"
    return f"{num:+,.0f}"


def _sign(value) -> int | None:
    num = _num(value)
    if num is None:
        return None
    if num > 0:
        return 1
    if num < 0:
        return -1
    return 0


def option_book_confirms_short(net_calls, net_puts) -> bool | None:
    """True when FII index options are short calls or long puts.

    net_calls and net_puts are long minus short, the participant-file sign.
    None when both sides are missing, so a distribution call is withheld.
    """
    calls = _num(net_calls)
    puts = _num(net_puts)
    if calls is None and puts is None:
        return None
    short_calls = calls is not None and calls < 0
    long_puts = puts is not None and puts > 0
    return short_calls or long_puts


def _majority(count: int, n: int) -> bool:
    return n > 0 and count * 2 > n


def overnight_positioning(fii_rows: list[dict] | None, futures_rows: list[dict] | None, *,
                          symbol: str = "NIFTY") -> dict:
    """Five-session FII drift joined to that index's aggregate futures quadrant."""
    symbol = (symbol or "").upper()
    empty = {
        "state": "insufficient",
        "line": "",
        "file_date": None,
        "n_fii": 0,
        "n_overlap": 0,
        "drift": None,
        "trend": "unknown",
        "long_days": 0,
        "short_days": 0,
        "net_fut_first": None,
        "net_fut_last": None,
    }
    if symbol == "SENSEX":
        empty["state"] = "not_on_exchange"
        empty["line"] = (
            "The NSE participant file does not include Sensex. "
            "Named FII positioning stays on Nifty and Bank Nifty."
        )
        return empty

    fii = [r for r in (fii_rows or []) if r.get("date") and _num(r.get("net_fut")) is not None]
    fut = {r.get("date"): r.get("agg_buildup") for r in (futures_rows or []) if r.get("date")}
    overlap = [r["date"] for r in fii if r["date"] in fut]
    empty["n_fii"] = len(fii)
    empty["n_overlap"] = len(overlap)
    scope = "NSE index futures, all NSE indices together, joined to aggregate " + symbol + " futures."
    if len(fii) < MIN_SESSIONS or len(overlap) < MIN_SESSIONS:
        empty["line"] = (
            f"Overnight book needs {MIN_SESSIONS} overlapping sessions and has "
            f"{len(fii)} FII days and {len(overlap)} aggregate futures days. {scope}"
        )
        return empty

    window = fii[-MIN_SESSIONS:]
    # Keep the drift on the same five dates that have a futures label.
    dated = [r for r in fii if r["date"] in fut][-MIN_SESSIONS:]
    drift = series_drift([r["net_fut"] for r in dated], tol_frac=0.10)
    trend = drift.get("trend") or "unknown"
    first = drift.get("first")
    last = drift.get("last")
    move = drift.get("drift")
    labels = [fut[r["date"]] for r in dated]
    long_days = sum(1 for label in labels if label == "long build-up")
    short_days = sum(1 for label in labels if label == "short build-up")
    n = len(dated)
    last_row = dated[-1]
    confirms = option_book_confirms_short(last_row.get("net_calls"), last_row.get("net_puts"))
    net_short = last is not None and last < 0
    file_date = last_row["date"]

    if trend == "falling" and net_short and confirms is False:
        state = "hedge"
    elif trend == "falling" and net_short and confirms is None:
        state = "unconfirmed"
    elif trend == "rising" and _majority(long_days, n):
        state = "accumulation"
    elif trend == "falling" and _majority(short_days, n) and (not net_short or confirms is True):
        state = "distribution"
    elif trend == "flat":
        state = "flat"
    else:
        state = "unconfirmed"

    level = ""
    if state == "accumulation" and net_short:
        level = " The level is still net short."
    hedge_bit = ""
    if state == "hedge":
        hedge_bit = " Short futures without short calls or long puts stay a cash hedge."
    if state == "unconfirmed" and confirms is None and net_short and trend == "falling":
        hedge_bit = " Option mix is missing, so this is not called distribution."

    line = (
        f"FII index-futures net {_contracts(first)} to {_contracts(last)} "
        f"({trend}, drift {_contracts(move)}) through {file_date}. "
        f"Aggregate {symbol} futures: long build-up {long_days} of {n} days, "
        f"short build-up {short_days} of {n}.{level}{hedge_bit} {scope}"
    )
    return {
        "state": state,
        "line": line,
        "file_date": file_date,
        "n_fii": len(fii),
        "n_overlap": n,
        "drift": None if move is None else round(float(move), 0),
        "trend": trend,
        "long_days": long_days,
        "short_days": short_days,
        "net_fut_first": first,
        "net_fut_last": last,
    }


def _hhmm(text) -> int | None:
    try:
        hour, minute = str(text).split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (ValueError, AttributeError):
        return None


def trailing_futures_change(rows: list[dict] | None, minutes: int = TRAIL_MINUTES) -> dict:
    """Price and aggregate-OI change. Trailing window when it has two prints, else the session."""
    parsed = []
    for row in rows or []:
        mins = _hhmm(row.get("time_ist"))
        price = _num(row.get("price"))
        oi = _num(row.get("agg_oi"))
        if mins is None or price is None or oi is None:
            continue
        parsed.append((mins, price, oi, row.get("time_ist")))
    parsed.sort()
    if len(parsed) < 2:
        return {"dprice": None, "doi": None, "price_now": None, "oi_start": None,
                "window": "unavailable", "start": None, "end": None, "n": len(parsed)}
    end_m = parsed[-1][0]
    trail = [row for row in parsed if end_m - row[0] <= minutes and end_m - row[0] >= 0]
    use = trail if len(trail) >= 2 else parsed
    window = "Last 30 minutes" if use is trail else "Session so far"
    dprice = use[-1][1] - use[0][1]
    doi = use[-1][2] - use[0][2]
    return {
        "dprice": dprice,
        "doi": doi,
        "price_now": use[-1][1],
        "oi_start": use[0][2],
        "window": window,
        "start": use[0][3],
        "end": use[-1][3],
        "n": len(use),
    }


def burst_notional(events: list[dict] | None, window_s: int = TRAIL_MINUTES * 60) -> dict:
    """Buy and sell burst notional over the trailing window ending at the newest event."""
    rows = []
    for event in events or []:
        if event.get("kind") != "burst":
            continue
        try:
            epoch = int(event.get("epoch"))
        except (TypeError, ValueError):
            continue
        notion = _num(event.get("notional")) or 0.0
        rows.append((epoch, event.get("side"), notion))
    if not rows:
        return {"buy": None, "sell": None, "n": 0}
    end = max(epoch for epoch, _side, _n in rows)
    buy = sell = 0.0
    n = 0
    for epoch, side, notion in rows:
        if end - epoch > window_s:
            continue
        n += 1
        if side == "buy":
            buy += notion
        elif side == "sell":
            sell += notion
    return {"buy": buy, "sell": sell, "n": n}


def cvd_window_change(bars: list[dict] | None, n: int = 30) -> float | None:
    """CVD change across the last n completed bars that carry a CVD value."""
    values = []
    for bar in bars or []:
        if isinstance(bar, dict):
            incomplete = bar.get("incomplete")
            raw = bar.get("cvd")
        else:
            incomplete = getattr(bar, "incomplete", False)
            raw = getattr(bar, "cvd", None)
        if incomplete:
            continue
        num = _num(raw)
        if num is not None:
            values.append(num)
    if len(values) < 2:
        return None
    use = values[-n:]
    return use[-1] - use[0]


def tape_status(label: str | None, cvd_change: float | None, buy, sell) -> str:
    """confirms, opposes, partial, quiet, or unavailable. Both CVD and bursts must agree to confirm."""
    if label not in _PAIR.values():
        if cvd_change is None and (buy is None or sell is None):
            return "unavailable"
        return "quiet"
    want = 1 if label == "long build-up" else -1
    cvd_s = _sign(cvd_change)
    flow_s = None if buy is None or sell is None else _sign(_num(buy) - _num(sell))
    if cvd_s is None and flow_s is None:
        return "unavailable"
    if cvd_s is None or flow_s is None:
        present = cvd_s if cvd_s is not None else flow_s
        if present == 0:
            return "quiet"
        return "opposes" if present != want else "partial"
    if cvd_s == want and flow_s == want:
        return "confirms"
    if cvd_s == -want or flow_s == -want:
        return "opposes"
    return "quiet"


def _crore(value) -> str:
    num = _num(value)
    if num is None:
        return "unavailable"
    crore = num / 1e7
    if abs(crore) >= 0.05:
        return f"₹{crore:.1f} Cr"
    return f"₹{num:,.0f}"


def live_flow(*, dprice, doi, price_now, oi_start, cvd_change, buy, sell,
              window: str, symbol: str = "NIFTY", source: str | None = None) -> dict:
    """Anonymous futures build-up for one index. Aggregate OI, not near-month."""
    price_tol = abs(_num(price_now) or 0.0) * LIVE_PRICE_TOL_FRAC
    oi_tol = max(1.0, abs(_num(oi_start) or 0.0) * LIVE_OI_TOL_FRAC)
    label = classify_futures_buildup(dprice, doi, tol_price=price_tol, tol_doi=oi_tol)
    status = tape_status(label, cvd_change, buy, sell)
    feed = f" {source} journal." if source else ""
    if label is None or window == "unavailable":
        line = f"{symbol} futures snapshots are short of a build-up read.{feed}"
    else:
        cvd_txt = "unavailable" if cvd_change is None else f"{cvd_change:+,.0f}"
        px = "unavailable" if dprice is None else f"{dprice:+,.1f}"
        oi = "unavailable" if doi is None else f"{doi:+,.0f}"
        line = (
            f"{window} on {symbol} futures: {label}. "
            f"Price {px}, aggregate open interest {oi}. "
            f"CVD {cvd_txt}. Buy bursts {_crore(buy)}, sell bursts {_crore(sell)}. "
            f"This tape has no participant name.{feed}"
        )
    return {
        "label": label,
        "line": line.strip(),
        "window": window,
        "tape": status,
        "dprice": dprice,
        "doi": doi,
        "cvd_change": cvd_change,
        "buy_notional": buy,
        "sell_notional": sell,
        "source": source,
    }


def align_clocks(overnight: dict, live: dict, *, file_is_previous_session: bool) -> dict:
    """Alert only on a fresh file, a matching build-up, and a confirming tape."""
    state = (overnight or {}).get("state")
    label = (live or {}).get("label")
    tape = (live or {}).get("tape")
    match = _PAIR.get(state) == label and label in _PAIR.values()
    alert = bool(match and tape == "confirms" and file_is_previous_session)

    if state == "not_on_exchange":
        headline = "No NSE participant read for Sensex."
    elif state == "hedge":
        headline = "Overnight book is a cash hedge."
    elif alert:
        headline = "Overnight book and today's tape agree."
    elif match and not file_is_previous_session:
        headline = "Build-up agrees. Participant file is older than the previous session."
    elif match and tape != "confirms":
        headline = "Build-up agrees. The tape did not confirm."
    elif state in _PAIR and label in ("long build-up", "short build-up", "short covering", "long unwinding"):
        headline = "Overnight book and today's tape disagree."
    elif state == "insufficient":
        headline = "Overnight book is short of five sessions."
    elif state == "unconfirmed":
        headline = "Overnight drift and the futures quadrant do not agree."
    elif state == "flat":
        headline = "Overnight futures book is flat."
    else:
        headline = "No positioning alert."
    return {"alert": alert, "headline": headline}
