"""sentinel/engine/rvol.py — Time-of-Day Relative Volume (RVol) Baseline.

Implements Sentinel §7.2:
  - Normalizes volume against the historical U-shaped intraday curve
  - RVol = Current Bar Volume / Baseline Minute Volume for that HH:MM
  - High RVol ≥ 1.5x, Climax RVol ≥ 2.5x

Baseline is an **asymmetric U-curve**. Shipped ``FROZEN_CURVES`` are the last
reviewed fit (2026-11-05..12). Live process prefers
``data/journal/rvol_baseline.json`` when fresh — written post-close by
``sentinel.engine.rvol_fit.refit_and_persist`` / health daemon / 
``scripts/log_rvol_baseline.py``.

CONT / ABS / CVD **gates are unchanged**. Only the denominator moves.
Do not lower ``rvol >= 1.5`` in the classifier to invent prints.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
# Session: 09:15 → 15:30 IST inclusive → 376 one-minute slots (0..375).
_OPEN_SLOT = 0
_MID_SLOT = 180   # 12:15 IST
_CLOSE_SLOT = 375  # 15:30 IST
_RIGHT_SPAN = float(_CLOSE_SLOT - _MID_SLOT)  # 195

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROFILE = _ROOT / "data" / "journal" / "rvol_baseline.json"

_live_lock = threading.Lock()
_live_curves: dict[str, "UCurve"] | None = None
_live_mtime: float | None = None
_live_source: str = "frozen"


class UCurve(NamedTuple):
    """Asymmetric U: open peak → midday trough → close peak (volume units)."""

    open_peak: float
    trough: float
    close_peak: float
    floor: float  # never below this (avoid explosive RVol on quiet prints)


# Shipped fallback. Fit: 2026-11-05,06,08,09,11,12 (holdout 13).
# open_peak = median(09:15); trough = median(11:45–12:45); close_peak = median(15:15).
# floor = 0.5 × trough. BANKNIFTY before NIFTY — substring rule.
FROZEN_CURVES: dict[str, UCurve] = {
    "NIFTY": UCurve(open_peak=138_776.0, trough=6_637.0, close_peak=11_771.0, floor=3_318.5),
    "BANKNIFTY": UCurve(open_peak=21_415.0, trough=1_416.5, close_peak=3_011.0, floor=708.25),
    "SENSEX": UCurve(open_peak=749.0, trough=159.5, close_peak=175.5, floor=79.75),
}

# Back-compat alias for older imports / tests.
_CURVES = FROZEN_CURVES

# Fit provenance for tests / operators.
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


def _curve_at(mins_from_open: int, curve: UCurve) -> float:
    """Asymmetric quadratic U evaluated at minutes-from-09:15."""
    t = int(mins_from_open)
    if t <= _MID_SLOT:
        frac = t / float(_MID_SLOT)  # 0 at open, 1 at mid
        level = curve.trough + (curve.open_peak - curve.trough) * ((1.0 - frac) ** 2)
    else:
        frac = (t - _MID_SLOT) / _RIGHT_SPAN  # 0 at mid, 1 at close
        frac = min(1.0, max(0.0, frac))
        level = curve.trough + (curve.close_peak - curve.trough) * (frac ** 2)
    return max(float(curve.floor), float(level))


def invalidate_live_curves() -> None:
    """Drop cached live profile (next read reloads from disk)."""
    global _live_curves, _live_mtime, _live_source
    with _live_lock:
        _live_curves = None
        _live_mtime = None
        _live_source = "frozen"


def baseline_source() -> str:
    """``frozen`` or ``live:<path>`` — for health / operator visibility."""
    _active_curves()
    return _live_source


def _active_curves(profile_path: Path | None = None) -> dict[str, UCurve]:
    """Prefer fresh journal profile; else shipped frozen curves."""
    global _live_curves, _live_mtime, _live_source
    path = Path(profile_path) if profile_path is not None else _DEFAULT_PROFILE
    with _live_lock:
        try:
            mtime = path.stat().st_mtime if path.exists() else None
        except OSError:
            mtime = None
        if (
            _live_curves is not None
            and mtime is not None
            and _live_mtime is not None
            and mtime == _live_mtime
        ):
            return _live_curves
        if mtime is None:
            _live_curves = dict(FROZEN_CURVES)
            _live_mtime = None
            _live_source = "frozen"
            return _live_curves
        try:
            from sentinel.engine.rvol_fit import (
                curves_from_payload,
                profile_is_fresh,
                read_profile,
            )

            payload = read_profile(path)
            if not payload or not profile_is_fresh(payload):
                log.info(
                    "rvol baseline profile missing/stale at %s — using frozen curves",
                    path,
                )
                _live_curves = dict(FROZEN_CURVES)
                _live_mtime = mtime
                _live_source = "frozen"
                return _live_curves
            curves = curves_from_payload(payload)
            if not curves:
                _live_curves = dict(FROZEN_CURVES)
                _live_mtime = mtime
                _live_source = "frozen"
                return _live_curves
            _live_curves = curves
            _live_mtime = mtime
            _live_source = f"live:{path}"
            return _live_curves
        except Exception as exc:
            log.warning("rvol live profile load failed (%s) — frozen: %s", path, exc)
            _live_curves = dict(FROZEN_CURVES)
            _live_mtime = mtime
            _live_source = "frozen"
            return _live_curves


def _get_tod_baseline(time_str: str, instrument: str = "NIFTY") -> float:
    """Return baseline 1m volume (units) for a given 'HH:MM' and index."""
    key = _index_key(instrument)
    curve = _active_curves()[key]
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
