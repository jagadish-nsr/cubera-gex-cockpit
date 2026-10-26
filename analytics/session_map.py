"""Session map for the web cockpit.

Numbers and locations only: per-strike open-interest change, the magnet on
each captured session, a parity reading at two strikes, the futures
price-and-open-interest pair, and the prior session's spot high, low, and close.
"""

from __future__ import annotations

from app.futures_buildup import classify_futures_buildup
from app.gammablast import crossover_state, pick_anchors, strike_premiums

_FUTURES_STATE = {
    "long build-up": "Price up, open interest up",
    "short build-up": "Price down, open interest up",
    "short covering": "Price up, open interest down",
    "long unwinding": "Price down, open interest down",
    "flat": "Price or open interest is flat",
}

_LOCATION = {
    "at_cross": "Call and put premiums are within 5 points. Spot is at this strike.",
    "call_rich": "Call premium is above the put. By parity, spot is above this strike.",
    "put_rich": "Put premium is above the call. By parity, spot is below this strike.",
}


def strike_doi_table(oi_strikes, spot: float, band_pts: float = 600.0) -> list[dict]:
    """Call and put open-interest change inside the band. No support or resistance label."""
    rows = []
    for cell in oi_strikes or []:
        strike = cell.get("strike")
        if strike is None:
            continue
        strike = float(strike)
        if abs(strike - float(spot)) > float(band_pts):
            continue
        call_doi = cell.get("call_doi")
        put_doi = cell.get("put_doi")
        if call_doi is None and put_doi is None:
            continue
        rows.append({
            "strike": strike,
            "call_doi": None if call_doi is None else int(round(float(call_doi))),
            "put_doi": None if put_doi is None else int(round(float(put_doi))),
        })
    rows.sort(key=lambda row: row["strike"])
    return rows


def magnet_closes(days: list[tuple[str, list]]) -> list[dict]:
    """Last positive magnet of each session, in date order."""
    out = []
    for date, magnets in days:
        last = None
        for raw in magnets or []:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                last = value
        if last is not None:
            out.append({"date": date, "magnet": last})
    return out


def prior_marks_from_spots(spots) -> dict | None:
    """High, low, and last spot of one captured session."""
    vals = []
    for raw in spots or []:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            vals.append(value)
    if not vals:
        return None
    return {"high": max(vals), "low": min(vals), "close": vals[-1]}


def describe_futures(dprice, doi, price=None, as_of: str | None = None) -> dict | None:
    """One of the four price-and-open-interest states, or flat. No direction arrow."""
    label = classify_futures_buildup(dprice, doi)
    if label is None:
        return None
    out = {
        "state": _FUTURES_STATE[label],
        "dprice": round(float(dprice), 1),
        "doi": round(float(doi), 0),
    }
    if price is not None:
        out["price"] = round(float(price), 1)
    if as_of:
        out["as_of"] = as_of
    return out


def crossover_watch(oi_strikes, spot: float, magnet: float) -> dict:
    """Parity location at the magnet strike and the at-the-money strike."""
    anchors = pick_anchors(oi_strikes, float(spot), float(magnet) if magnet else None)
    rows = []
    for key, name in (("magnet", "Magnet"), ("atm", "ATM")):
        strike = anchors.get(key)
        call, put = strike_premiums(oi_strikes, strike)
        state = crossover_state(call, put)
        rows.append({
            "name": name,
            "strike": None if strike is None else float(strike),
            "call": None if call is None else round(float(call), 1),
            "put": None if put is None else round(float(put), 1),
            "side": None if state is None else state["side"],
            "diff": None if state is None else round(float(state["diff"]), 1),
            "location": "No paired premiums at this strike." if state is None else _LOCATION[state["side"]],
        })
    return {"anchors": rows}


def load_magnet_path(symbol: str, n: int = 8) -> list[dict]:
    """Last magnet from each of the newest captured session files."""
    from app.timeseries import TS_DIR, load_day

    folder = TS_DIR / symbol.upper()
    if not folder.is_dir():
        return []
    names = sorted(path.stem for path in folder.glob("*.csv") if len(path.stem) == 10)
    days = []
    for name in names[-n:]:
        frame = load_day(name, symbol)
        magnets = []
        if frame is not None and not frame.empty and "magnet" in frame.columns:
            magnets = frame["magnet"].tolist()
        days.append((name, magnets))
    return magnet_closes(days)


def load_prior_marks(symbol: str, today: str) -> dict | None:
    """Spot high, low, and last print of the captured session before ``today``."""
    import pandas as pd

    from app.timeseries import TS_DIR, load_day

    folder = TS_DIR / symbol.upper()
    if not folder.is_dir():
        return None
    names = sorted(
        path.stem for path in folder.glob("*.csv")
        if len(path.stem) == 10 and path.stem < today
    )
    if not names:
        return None
    frame = load_day(names[-1], symbol)
    if frame is None or frame.empty or "spot" not in frame.columns:
        return None
    spots = pd.to_numeric(frame["spot"], errors="coerce").dropna().tolist()
    marks = prior_marks_from_spots(spots)
    if marks is None:
        return None
    marks["date"] = names[-1]
    return marks


def read_futures_state(symbol: str, date_ist: str) -> dict | None:
    """Session futures change when two snapshots exist, otherwise the last saved day."""
    from app.futures_buildup import (
        available_days,
        intraday_futures_buildup,
        load_futures_day,
        load_futures_intraday_day,
    )

    try:
        frame = load_futures_intraday_day(date_ist, symbol)
        built = intraday_futures_buildup(frame)
    except Exception:
        built = None
    if built and built.get("dprice") is not None and built.get("doi") is not None:
        return describe_futures(
            built["dprice"], built["doi"],
            price=built.get("price_now"),
            as_of=str(built.get("end_time") or date_ist),
        )
    try:
        days = available_days(symbol)
    except Exception:
        days = []
    days = [day for day in days if str(day) <= date_ist]
    if not days:
        return None
    record = load_futures_day(days[-1], symbol) or {}
    if record.get("dprice") is None or record.get("agg_doi") is None:
        return None
    return describe_futures(
        record["dprice"], record["agg_doi"],
        price=record.get("close"),
        as_of=days[-1],
    )
