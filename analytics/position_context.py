"""Participant holdings and the prior session value-area line.

Open interest here is end-of-day long and short counts. The value-area line is
the prior session's point of control and value high and low.
"""

from __future__ import annotations

_PARTICIPANTS = ("Client", "FII", "DII", "Pro")
_HOLDING_FIELDS = (
    "fut_long", "fut_short",
    "opt_call_long", "opt_call_short",
    "opt_put_long", "opt_put_short",
)


def holdings_table(parsed: dict | None, date_ist: str | None) -> dict:
    """Long and short counts for Client, FII, DII, and Pro."""
    rows = []
    book = parsed or {}
    for name in _PARTICIPANTS:
        cell = book.get(name) or {}
        rows.append({
            "participant": name,
            **{field: cell.get(field) for field in _HOLDING_FIELDS},
        })
    return {"date": date_ist, "rows": rows}


def holdings_for(on_or_before: str | None = None) -> dict:
    """Latest holdings file, or the last file dated on or before ``on_or_before``."""
    from app.participant_oi import available_days, latest_available, load_participant_day

    if on_or_before:
        days = [day for day in available_days() if day <= on_or_before]
        if not days:
            return holdings_table(None, None)
        return holdings_table(load_participant_day(days[-1]), days[-1])
    latest = latest_available()
    if not latest:
        return holdings_table(None, None)
    return holdings_table(latest[1], latest[0])


def value_area_line(prior: dict | None, spot: float, prior_day: str | None) -> dict:
    """One sentence: prior POC, value high, value low, and whether spot is inside."""
    if not prior or prior.get("poc") is None or prior.get("vah") is None or prior.get("val") is None:
        return {
            "date": prior_day,
            "poc": None,
            "vah": None,
            "val": None,
            "inside": None,
            "text": "No prior value area.",
        }
    poc = float(prior["poc"])
    vah = float(prior["vah"])
    val = float(prior["val"])
    inside = val <= float(spot) <= vah
    place = "inside" if inside else "outside"
    day = prior_day or "the prior session"
    return {
        "date": prior_day,
        "poc": round(poc, 1),
        "vah": round(vah, 1),
        "val": round(val, 1),
        "inside": inside,
        "text": (
            f"Prior value {day}: POC ₹{poc:,.0f}, VAH ₹{vah:,.0f}, VAL ₹{val:,.0f}. "
            f"Spot is {place}."
        ),
    }


def value_area_for(symbol: str, today: str, spot: float, source: str | None = None) -> dict:
    """Prior session value area. Days after ``today`` are not read."""
    try:
        from app.market_profile import overlay_profiles

        bundle = overlay_profiles(symbol, source, today, weekly=False, live_spot=None)
    except Exception:
        return value_area_line(None, spot, None)
    return value_area_line(
        (bundle or {}).get("prior"),
        spot,
        (bundle or {}).get("prior_day"),
    )
