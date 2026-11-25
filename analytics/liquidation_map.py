"""Margin-stress map for index futures.

An OI increase opens a long and a short together. A long that posted only the
exchange minimum is stressed near entry * (1 - r * f). A short is stressed near
entry * (1 + r * f). r is SPAN plus exposure as a fraction of notional. f is
the fraction of that margin used as the square-off point.

NSE does not publish forced-liquidation orders. An OI drop is an observed
unwind only when that bar's range trades through the stress price. Anything
else is voluntary cover and stays off the observed layer.
"""

from __future__ import annotations

import math
from collections import deque


_READING = (
    "Minimum-margin stress estimate: long inventory below the entry, short inventory above. "
    "NSE does not publish who was squared off. The client file is a share, not an account list. "
    "Open interest lags. Not a fade and not a cascade."
)


def _require_margins(
    margin_frac_long: float | None,
    margin_frac_short: float | None,
    square_off_frac: float | None,
) -> tuple[float, float, float]:
    if margin_frac_long is None or margin_frac_short is None:
        raise ValueError("margin_frac is unset")
    if square_off_frac is None:
        raise ValueError("square_off_frac is unset")
    r_long = float(margin_frac_long)
    r_short = float(margin_frac_short)
    f = float(square_off_frac)
    for name, r in (("margin_frac_long", r_long), ("margin_frac_short", r_short)):
        if not math.isfinite(r) or r <= 0 or r >= 1:
            raise ValueError(f"{name} must be a fraction of notional between 0 and 1")
    if not math.isfinite(f) or f <= 0 or f > 1:
        raise ValueError("square_off_frac must be in (0, 1]")
    return r_long, r_short, f


def margin_frac_from_span(
    worst_scan_loss_per_lot: float,
    spot: float,
    lot_size: int,
    exposure_frac: float,
) -> float:
    """r = SPAN scan loss per lot / notional + exposure, as a fraction of notional.

    ``worst_scan_loss_per_lot`` is the largest of the 16 SPAN risk-array losses for
    one futures lot, in rupees. Notional is spot * lot_size. Exposure is NSE's
    percentage of notional (3% for index futures). Both parts are required.
    """
    if worst_scan_loss_per_lot is None or not math.isfinite(float(worst_scan_loss_per_lot)):
        raise ValueError("SPAN scan loss is missing")
    if worst_scan_loss_per_lot < 0:
        raise ValueError("SPAN scan loss must be a non-negative loss")
    if not spot or spot <= 0 or not lot_size or lot_size <= 0:
        raise ValueError("spot and lot_size must be positive")
    if exposure_frac is None or not (0 < float(exposure_frac) < 1):
        raise ValueError("exposure_frac must be a fraction between 0 and 1")
    notional = float(spot) * int(lot_size)
    return float(worst_scan_loss_per_lot) / notional + float(exposure_frac)


def stress_prices(
    entry: float,
    margin_frac_long: float,
    margin_frac_short: float,
    square_off_frac: float,
) -> tuple[float, float]:
    """Long stress below entry and short stress above it, each on its own SPAN side."""
    r_long, r_short, f = _require_margins(margin_frac_long, margin_frac_short, square_off_frac)
    return entry * (1.0 - r_long * f), entry * (1.0 + r_short * f)


def _bin_floor(price: float, step: float) -> float:
    return math.floor((price / step) + 1e-9) * step


def _overlaps(low: float, high: float, price: float) -> bool:
    pad = 1e-6 * max(1.0, abs(price))
    return (low - pad) <= price <= (high + pad)


def _shares(
    symbol: str,
    client_long: float | None,
    client_short: float | None,
    total_long: float | None,
    total_short: float | None,
    participant_age_sessions: int | None,
) -> tuple[float, float, bool, bool]:
    """Long share, short share, client_split, participant_stale.

    Sensex has no NSE participant file. A file older than three sessions is
    dropped and the map uses total OI.
    """
    stale = participant_age_sessions is not None and int(participant_age_sessions) > 3
    if symbol == "SENSEX" or stale:
        return 1.0, 1.0, False, stale
    if None in (client_long, client_short, total_long, total_short):
        return 1.0, 1.0, False, False
    total_l = float(total_long)
    total_s = float(total_short)
    if total_l <= 0 or total_s <= 0:
        return 1.0, 1.0, False, False
    return float(client_long) / total_l, float(client_short) / total_s, True, False


def _close_side(
    cohorts: deque[dict],
    side: str,
    qty: float,
    low: float,
    high: float,
    step: float,
    unwind: dict[float, float],
    count_unwind: bool,
) -> None:
    """FIFO-close ``qty`` lots on one side. Mark an unwind only on a price overlap."""
    left = qty
    stress_key = "long_stress" if side == "long" else "short_stress"
    open_key = "long_open" if side == "long" else "short_open"
    idx = 0
    while left > 1e-9 and idx < len(cohorts):
        cohort = cohorts[idx]
        open_lots = cohort[open_key]
        if open_lots <= 1e-9:
            idx += 1
            continue
        take = min(left, open_lots)
        cohort[open_key] = open_lots - take
        left -= take
        if count_unwind and _overlaps(low, high, cohort[stress_key]):
            b = _bin_floor(cohort[stress_key], step)
            unwind[b] = unwind.get(b, 0.0) + take
        if cohort["long_open"] <= 1e-9 and cohort["short_open"] <= 1e-9:
            del cohorts[idx]
        else:
            idx += 1


def build_liquidation_map(
    bars: list[dict],
    *,
    margin_frac_long: float | None,
    margin_frac_short: float | None,
    square_off_frac: float | None,
    bin_step: float,
    symbol: str,
    spot: float | None = None,
    client_long: float | None = None,
    client_short: float | None = None,
    total_long: float | None = None,
    total_short: float | None = None,
    participant_age_sessions: int | None = None,
    participant_date: str | None = None,
    sessions_to_expiry: int | None = None,
    agg_doi: float | None = None,
) -> dict:
    """Bin remaining inventory and OI that left while price traded the stress.

    Each bar is ``{close, high, low}`` plus either ``doi`` or absolute ``oi``.
    The first absolute-OI bar is opening stock with an unknown entry, so it
    does not seed a band. Later bars seed on an OI rise and close FIFO on a fall.
    """
    r_long, r_short, f = _require_margins(margin_frac_long, margin_frac_short, square_off_frac)
    step = float(bin_step)
    if not math.isfinite(step) or step <= 0:
        raise ValueError("bin_step must be positive")
    long_share, short_share, client_split, participant_stale = _shares(
        symbol,
        client_long,
        client_short,
        total_long,
        total_short,
        participant_age_sessions,
    )
    roll_window = sessions_to_expiry is not None and 0 <= int(sessions_to_expiry) <= 3
    agg_fell = agg_doi is not None and float(agg_doi) < 0
    count_unwind = not (roll_window and not agg_fell)

    cohorts: deque[dict] = deque()
    long_unwind: dict[float, float] = {}
    short_unwind: dict[float, float] = {}
    prev_oi: float | None = None
    opening_oi_unplaced = 0.0

    for bar in bars:
        close = float(bar["close"])
        high = float(bar.get("high", close))
        low = float(bar.get("low", close))
        if "doi" in bar and bar["doi"] is not None:
            doi = float(bar["doi"])
        else:
            oi = bar.get("oi")
            if oi is None:
                continue
            oi_now = float(oi)
            if prev_oi is None:
                opening_oi_unplaced = oi_now
                prev_oi = oi_now
                continue
            doi = oi_now - prev_oi
            prev_oi = oi_now
        if doi > 1e-9:
            long_px, short_px = stress_prices(close, r_long, r_short, f)
            cohorts.append({
                "long_stress": long_px,
                "short_stress": short_px,
                "long_open": doi * long_share,
                "short_open": doi * short_share,
            })
        elif doi < -1e-9:
            drop = -doi
            _close_side(cohorts, "long", drop * long_share, low, high, step, long_unwind, count_unwind)
            _close_side(cohorts, "short", drop * short_share, low, high, step, short_unwind, count_unwind)

    long_heat: dict[float, float] = {}
    short_heat: dict[float, float] = {}
    for cohort in cohorts:
        if cohort["long_open"] > 1e-9:
            b = _bin_floor(cohort["long_stress"], step)
            long_heat[b] = long_heat.get(b, 0.0) + cohort["long_open"]
        if cohort["short_open"] > 1e-9:
            b = _bin_floor(cohort["short_stress"], step)
            short_heat[b] = short_heat.get(b, 0.0) + cohort["short_open"]

    prices = sorted(set(long_heat) | set(short_heat) | set(long_unwind) | set(short_unwind))
    bins = []
    for price in prices:
        row = {
            "price": round(price, 4),
            "long_heat": round(long_heat.get(price, 0.0), 4),
            "short_heat": round(short_heat.get(price, 0.0), 4),
            "long_unwind": round(long_unwind.get(price, 0.0), 4),
            "short_unwind": round(short_unwind.get(price, 0.0), 4),
        }
        if any(row[k] for k in ("long_heat", "short_heat", "long_unwind", "short_unwind")):
            bins.append(row)

    return {
        "symbol": symbol,
        "empty": not bins,
        "reason": None if bins else "no_oi_change",
        "bins": bins,
        "spot": None if spot is None else float(spot),
        "margin_frac_long": r_long,
        "margin_frac_short": r_short,
        "square_off_frac": f,
        "bin_step": step,
        "client_split": client_split,
        "participant_stale": participant_stale,
        "participant_date": participant_date,
        "long_share": round(long_share, 6),
        "short_share": round(short_share, 6),
        "roll_window": roll_window,
        "observed_suppressed": not count_unwind,
        "agg_doi": None if agg_doi is None else float(agg_doi),
        "opening_oi_unplaced": round(opening_oi_unplaced, 4),
        "reading": _READING,
    }
