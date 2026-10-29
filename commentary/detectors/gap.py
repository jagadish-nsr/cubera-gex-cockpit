"""commentary/detectors/gap.py — D1 Opening Gap & Gap Fill Detector."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from commentary.config_loader import get_detector_config
from commentary.detectors.base import BaseDetector
from commentary.models import Event, PointInTimeView, StoryState

# A prior close this far from the live spot is not this index's previous
# session. The stored false gaps were 7.3% and 8.2%. A normal cash gap is
# inside this band; the 15% circuit check below is the hard backstop.
_MAX_PRIOR_VS_SPOT = 0.06

# The cash open is the 09:15 print. After 09:20 the live spot has left the
# open, and a futures candle in that window is the basis, not the gap.
_OPEN_START_MIN = 9 * 60 + 15
_OPEN_END_MIN = 9 * 60 + 20
_JOURNAL = Path(__file__).resolve().parents[2] / "data" / "journal" / "timeseries"


def _clock_minute(as_of: datetime) -> int:
    return as_of.hour * 60 + as_of.minute


def _journal_cash_open(symbol: str, session_date: str) -> float:
    """09:15 cash spot from the timeseries journal, or 0 when that bar is absent."""
    path = _JOURNAL / symbol.upper() / f"{session_date}.csv"
    if not path.is_file():
        return 0.0
    try:
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("time_ist") or "").startswith("09:15"):
                    return float(row.get("spot") or 0.0)
    except (OSError, TypeError, ValueError):
        return 0.0
    return 0.0


class OpeningGapDetector(BaseDetector):
    """Detects opening gap size, sigma magnitude, and relation to major GEX walls."""

    def __init__(self):
        cfg = get_detector_config("gap")
        cooldown = cfg.get("cooldown_minutes", 30)
        super().__init__(name="opening_gap", cooldown_minutes=cooldown)
        self.min_gap_sigma = cfg.get("min_gap_sigma", 0.35)
        self._initial_gap_fired = False
        self._open_price: float | None = None
        self._prior_close: float | None = None
        self._initial_gap_pts: float = 0.0
        self._journal_open: float | None = None
        self._restored_day: str | None = None

    def _restore_published(self, pit_view: PointInTimeView) -> None:
        """A restart keeps the gap that was already told. It does not measure a new one."""
        day = pit_view.as_of.date().isoformat()
        if self._initial_gap_fired or self._restored_day == day:
            return
        self._restored_day = day
        try:
            from commentary.storage.db import find_session_gap
            saved = find_session_gap(pit_view.symbol, pit_view.as_of.date().isoformat())
        except Exception:
            saved = None
        if not saved:
            return
        try:
            self._open_price = float(saved.get("open") or 0.0) or None
            self._prior_close = float(saved.get("prior_close") or 0.0) or None
            self._initial_gap_pts = float(saved.get("gap_pts") or 0.0)
        except (TypeError, ValueError):
            return
        self._initial_gap_fired = True

    def _cash_open(self, pit_view: PointInTimeView) -> float | None:
        """Cash open. A futures candle is the basis, not this print."""
        minute = _clock_minute(pit_view.as_of)
        if _OPEN_START_MIN <= minute <= _OPEN_END_MIN:
            return float(pit_view.spot)
        if self._journal_open is None:
            found = _journal_cash_open(pit_view.symbol, pit_view.as_of.date().isoformat())
            if found > 0:
                self._journal_open = found
        opened = float(self._journal_open or 0.0)
        if opened <= 0 or pit_view.prior_close <= 0:
            return None
        if abs(opened - pit_view.prior_close) / pit_view.prior_close > _MAX_PRIOR_VS_SPOT:
            return None
        if pit_view.spot > 0 and abs(opened - pit_view.spot) / pit_view.spot > _MAX_PRIOR_VS_SPOT:
            return None
        return opened

    def detect(self, pit_view: PointInTimeView, state: StoryState) -> list[Event]:
        events: list[Event] = []

        if pit_view.prior_close <= 0 or pit_view.spot <= 0:
            return events

        # The reference has to be this index's previous close. A 24,500
        # placeholder against a ~22,700 book is not an opening gap.
        if abs(pit_view.prior_close - pit_view.spot) / pit_view.spot > _MAX_PRIOR_VS_SPOT:
            return events

        open_p = self._cash_open(pit_view)

        if not self._initial_gap_fired:
            if open_p is None:
                return events
            gap_pts = open_p - pit_view.prior_close
            gap_pct = (gap_pts / pit_view.prior_close) * 100.0

            # Guard against absurd gaps (> 15% circuit breaker limit)
            if abs(gap_pct) > 15.0:
                return events

            sigma_1d = pit_view.sigma_1d
            gap_sigma = gap_pts / sigma_1d if sigma_1d > 0 else 0.0

            if abs(gap_sigma) >= self.min_gap_sigma:
                self._open_price = open_p
                self._prior_close = pit_view.prior_close
                self._initial_gap_pts = gap_pts
                self._initial_gap_fired = True
                self.mark_fired(pit_view.as_of, level=open_p)

                evt_type = "GAP_UP" if gap_pts > 0 else "GAP_DOWN"
                nearest_wall = pit_view.call_wall if gap_pts > 0 else pit_view.put_wall
                dist_wall = abs(open_p - nearest_wall)

                facts = {
                    "symbol": pit_view.symbol,
                    "open": round(open_p, 1),
                    "prior_close": round(pit_view.prior_close, 1),
                    "gap_pts": round(gap_pts, 1),
                    "gap_pct": round(gap_pct, 2),
                    "gap_sigma": round(gap_sigma, 2),
                    "nearest_wall": round(nearest_wall, 1),
                    "dist_to_wall": round(dist_wall, 1),
                    "call_wall": round(pit_view.call_wall, 1),
                    "put_wall": round(pit_view.put_wall, 1),
                }

                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_{evt_type}",
                        ts=pit_view.as_of,
                        type=evt_type,
                        facts=facts,
                        magnitude_sigma=abs(gap_sigma),
                        quality_flags=[],
                    )
                )
            else:
                # Below minimum gap threshold
                self._initial_gap_fired = True
                return events

        # Follow-up: Gap fill evaluation if gap exists and session is underway
        elif self._open_price and abs(self._initial_gap_pts) > 5.0 and not self.is_cooling_down(pit_view.as_of):
            # Check how much of initial gap is retraced
            # If gap was +100, and current spot is open - 50, retrace = 50%
            current_spot = pit_view.spot
            retrace_pts = self._open_price - current_spot if self._initial_gap_pts > 0 else current_spot - self._open_price
            retrace_pct = (retrace_pts / abs(self._initial_gap_pts)) * 100.0

            if retrace_pct >= 90.0:
                self.mark_fired(pit_view.as_of, level=current_spot)
                events.append(
                    Event(
                        id=f"evt_{int(pit_view.as_of.timestamp())}_GAP_FILLED",
                        ts=pit_view.as_of,
                        type="GAP_FILLED",
                        facts={
                            "open": round(self._open_price, 1),
                            "prior_close": round(self._prior_close or 0.0, 1),
                            "current_spot": round(current_spot, 1),
                            "retrace_pct": round(retrace_pct, 1),
                        },
                        magnitude_sigma=0.5,
                    )
                )

        return events
