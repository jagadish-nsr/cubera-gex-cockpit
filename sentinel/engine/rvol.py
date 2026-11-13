"""sentinel/engine/rvol.py — Time-of-Day Relative Volume (RVol) Baseline.

Implements Sentinel §7.2:
  - Normalizes volume against the historical U-shaped intraday curve
  - 09:15–09:30 has several× midday volume
  - RVol = Current Bar Volume / Baseline Minute Volume for that HH:MM
  - High RVol ≥ 1.5x, Climax RVol ≥ 2.5x

Baseline is an **asymmetric U-curve** fitted to median 1m futures volume
(prod ``sentinel_bars``, fit days 2026-11-05..12, holdout 2026-11-13):

  - Old analytical Nifty midday (~12k) made RVol≥1.5 almost impossible after
    the open (13-Nov: 0/73 CONT-shaped bars cleared RVol) — false silence.
  - Old Sensex midday (~60) made RVol≥1.5 on ~50% of bars — false flood.
  - CONT / ABS / CVD **gates are unchanged**. Only the denominator moves.

VERIFY: re-fit if NSE/BSE futures volume regime shifts (lot change, product
migration). Do not lower ``rvol >= 1.5`` in the classifier to invent prints.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import NamedTuple

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
# Session: 09:15 → 15:30 IST inclusive → 376 one-minute slots (0..375).
_OPEN_SLOT = 0
_MID_SLOT = 180   # 12:15 IST
_CLOSE_SLOT = 375  # 15:30 IST
_RIGHT_SPAN = float(_CLOSE_SLOT - _MID_SLOT)  # 195


class _UCurve(NamedTuple):
    """Asymmetric U: open peak → midday trough → close peak (volume units)."""

    open_peak: float
    trough: float
    close_peak: float
    floor: float  # never below this (avoid explosive RVol on quiet prints)


# Empirical median 1m volumes. Fit: 2026-11-05,06,08,09,11,12 (holdout 13).
# open_peak = median(09:15); trough = median(11:45–12:45); close_peak = median(15:15).
# floor = 0.5 × trough. BANKNIFTY before NIFTY — substring rule.
_CURVES: dict[str, _UCurve] = {
    "NIFTY": _UCurve(open_peak=138_776.0, trough=6_637.0, close_peak=11_771.0, floor=3_318.5),
    "BANKNIFTY": _UCurve(open_peak=21_415.0, trough=1_416.5, close_peak=3_011.0, floor=708.25),
    "SENSEX": _UCurve(open_peak=749.0, trough=159.5, close_peak=175.5, floor=79.75),
}

# Fit provenance for tests / operators (not used at runtime).
BASELINE_FIT_DAYS = (
    "2026-11-05",
    "2026-11-06",
    "2026-11-08",
    "2026-11-09",
    "2026-11-11",
    "2026-11-12",
)
BASELINE_HOLDOUT_DAY = "2026-11-13"


def _index_key(instrument: str) -> str:
    """Map instrument id to curve key. BANKNIFTY before NIFTY (substring)."""
    raw = (instrument or "").upper().replace("-FUT", "").strip()
    if "SENSEX" in raw:
        return "SENSEX"
    if "BANKNIFTY" in raw:
        return "BANKNIFTY"
    return "NIFTY"


def _mins_from_open(time_str: str) -> int:
    """Minutes after 09:15; clamped to the continuous session [0, 375]."""
    try:
        parts = time_str.split(":")
        hh = int(parts[0])
        mm = int(parts[1])
        mins = (hh - 9) * 60 + (mm - 15)
    except Exception:
        return _MID_SLOT
    return max(_OPEN_SLOT, min(_CLOSE_SLOT, mins))


def _curve_at(mins_from_open: int, curve: _UCurve) -> float:
    """Asymmetric quadratic U evaluated at minutes-from-09:15."""
    t = int(mins_from_open)
    if t <= _MID_SLOT:
        frac = t / float(_MID_SLOT)  # 0 at open, 1 at mid
        # (1-frac)^2: full open peak at t=0, trough at mid
        level = curve.trough + (curve.open_peak - curve.trough) * ((1.0 - frac) ** 2)
    else:
        frac = (t - _MID_SLOT) / _RIGHT_SPAN  # 0 at mid, 1 at close
        frac = min(1.0, max(0.0, frac))
        level = curve.trough + (curve.close_peak - curve.trough) * (frac ** 2)
    return max(float(curve.floor), float(level))


def _get_tod_baseline(time_str: str, instrument: str = "NIFTY") -> float:
    """Return baseline 1m volume (units) for a given 'HH:MM' and index."""
    key = _index_key(instrument)
    curve = _CURVES[key]
    return _curve_at(_mins_from_open(time_str), curve)


def compute_tod_rvol(
    timestamp: datetime,
    volume: float,
    instrument: str = "NIFTY",
) -> float:
    """Compute Time-of-Day Normalized Relative Volume (RVol).

    Returns:
        float: RVol factor (1.0 = typical for that minute; 2.5 = climax).
    """
    if volume <= 0:
        return 0.0

    if timestamp.tzinfo is not None:
        ist_time = timestamp.astimezone(_IST)
    else:
        ist_time = timestamp

    time_str = ist_time.strftime("%H:%M")
    baseline = _get_tod_baseline(time_str, instrument=instrument)
    if baseline <= 0:
        log.warning("Non-positive TOD baseline for %s at %s — refusing RVol", instrument, time_str)
        return 0.0

    return round(float(volume) / baseline, 2)
