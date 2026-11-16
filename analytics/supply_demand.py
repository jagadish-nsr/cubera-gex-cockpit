"""Rule-based supply / demand boxes from 1-min futures OHLC.

Explicit geometry (hand-checkable):

- **Base**: ``base_bars`` consecutive bars whose range (max high − min low) is
  ≤ ``base_width_max`` (defaults to the impulse threshold).
- **Demand (drop–base–rally)**: after the base, price rallies at least
  ``impulse_pts`` within ``impulse_bars``, measured from the base high. The
  box is [base_low, base_high].
- **Supply (rally–base–drop)**: after the base, price drops at least
  ``impulse_pts`` within ``impulse_bars``, measured from the base low. The
  box is [base_low, base_high].

Mitigation: a demand box is mitigated when a later close trades through
``base_low``; a supply box when a later close trades through ``base_high``.
Mitigated boxes are dropped unless ``keep_mitigated=True``.

These are reaction-candidate zones, not guaranteed fades.
"""

from __future__ import annotations

from typing import Any, Iterable

# Minimum departure (points) to count as an impulse away from the base.
IMPULSE_PTS = {
    "NIFTY": 35.0,
    "BANKNIFTY": 80.0,
    "SENSEX": 80.0,
}


def impulse_pts_for(symbol: str | None, strike_step: float | None = None) -> float:
    from config import index_key

    key = index_key(symbol)
    if key in IMPULSE_PTS:
        return float(IMPULSE_PTS[key])
    step = float(strike_step or 50.0)
    return max(25.0, step * 0.7)


def _bars(rows: Iterable[Any]) -> list[dict]:
    out = []
    if rows is None:
        return out
    if hasattr(rows, "to_dict") and hasattr(rows, "columns"):
        try:
            iterable = rows.to_dict("records")
        except Exception:
            iterable = []
    else:
        iterable = rows
    for raw in iterable:
        if hasattr(raw, "to_dict") and not isinstance(raw, dict):
            try:
                row = raw.to_dict()
            except Exception:
                continue
        elif isinstance(raw, dict):
            row = raw
        else:
            continue
        try:
            o = float(row["open"])
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if min(o, h, l, c) <= 0 or h < l:
            continue
        out.append({
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "time_ist": str(row.get("time_ist") or ""),
        })
    return out


def detect_supply_demand(
    bars: Iterable[Any],
    *,
    impulse_pts: float,
    base_bars: int = 4,
    impulse_bars: int = 6,
    base_width_max: float | None = None,
    max_supply: int = 2,
    max_demand: int = 2,
    keep_mitigated: bool = False,
    day: str | None = None,
) -> list[dict]:
    """Scan left→right for rally-base-drop (supply) and drop-base-rally (demand)."""
    series = _bars(bars)
    n = len(series)
    need = int(base_bars) + int(impulse_bars)
    if n < need + 2 or impulse_pts <= 0:
        return []

    width_cap = float(base_width_max if base_width_max is not None else impulse_pts)
    zones: list[dict] = []
    used_ends: set[int] = set()

    i = int(base_bars)
    while i <= n - int(impulse_bars):
        base = series[i - int(base_bars) : i]
        base_lo = min(b["low"] for b in base)
        base_hi = max(b["high"] for b in base)
        base_width = base_hi - base_lo
        if base_width <= 0 or base_width > width_cap:
            i += 1
            continue

        window = series[i : i + int(impulse_bars)]
        max_close = max(b["close"] for b in window)
        min_close = min(b["close"] for b in window)
        rally = max_close - base_hi
        drop = base_lo - min_close

        side = None
        departure = 0.0
        if rally >= float(impulse_pts) and rally >= drop:
            side = "demand"
            departure = float(rally)
        elif drop >= float(impulse_pts):
            side = "supply"
            departure = float(drop)

        if side is None:
            i += 1
            continue

        # Skip overlapping bases that share the same departure window end.
        end_i = i + int(impulse_bars) - 1
        if end_i in used_ends:
            i += 1
            continue
        used_ends.add(end_i)

        # Mitigation against bars after the impulse window.
        mitigated = False
        for b in series[end_i + 1 :]:
            if side == "demand" and b["close"] < base_lo:
                mitigated = True
                break
            if side == "supply" and b["close"] > base_hi:
                mitigated = True
                break

        if mitigated and not keep_mitigated:
            i = end_i + 1
            continue

        mid = (base_lo + base_hi) / 2.0
        zones.append({
            "side": side,
            "lo": round(base_lo, 1),
            "hi": round(base_hi, 1),
            "mid": round(mid, 1),
            "width": round(base_width, 1),
            "impulse_pts": round(departure, 1),
            "strength": round(departure / max(base_width, 1.0), 2),
            "formed_at": base[-1]["time_ist"] or None,
            "mitigated": mitigated,
            "day": day,
            "base_bars": int(base_bars),
            "impulse_bars": int(impulse_bars),
            "rule": "drop_base_rally" if side == "demand" else "rally_base_drop",
        })
        # Jump past this base so one impulse does not mint many overlapping boxes.
        i = end_i + 1

    # Prefer unmitigated, then strongest departure, keep nearest caps by side.
    demand = [z for z in zones if z["side"] == "demand"]
    supply = [z for z in zones if z["side"] == "supply"]
    demand.sort(key=lambda z: (z["mitigated"], -z["impulse_pts"]))
    supply.sort(key=lambda z: (z["mitigated"], -z["impulse_pts"]))
    return demand[: max(0, int(max_demand))] + supply[: max(0, int(max_supply))]


def zones_nearest_spot(zones: list[dict], spot: float, *, max_each: int = 2) -> list[dict]:
    """Keep demand at/below spot and supply at/above spot (classic sandwich).

    Falls back to absolute-nearest of that side only when no correctly-sided
    zone exists — so a wrong-side box does not sit next to its opposite.
    """
    try:
        spot_f = float(spot)
    except (TypeError, ValueError):
        return list(zones or [])

    def _mid(z: dict) -> float:
        return float(z["mid"])

    def _pick(side: str, prefer) -> list[dict]:
        pool = [z for z in zones if z.get("side") == side]
        sided = [z for z in pool if prefer(z)]
        chosen = sided if sided else pool
        chosen = sorted(chosen, key=lambda z: abs(_mid(z) - spot_f))
        return chosen[: max(0, int(max_each))]

    # Demand should sit under price; supply should sit over price.
    demand = _pick("demand", lambda z: _mid(z) <= spot_f)
    supply = _pick("supply", lambda z: _mid(z) >= spot_f)
    return demand + supply
