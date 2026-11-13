"""Continuous empirical fit of TOD RVol U-curves from ``sentinel_bars``.

Post-close (and via ``scripts/log_rvol_baseline.py``) this module:

1. Reads recent complete session days from ``data/sentinel.db``
2. Fits asymmetric U-curve anchors (09:15 / midday trough / 15:15)
3. **Validates** that RVol≥1.5 prevalence stays in a selective band
4. Writes ``data/journal/rvol_baseline.json`` only when every index passes

CONT / ABS / CVD classifier thresholds are never modified here — only the
volume denominator used by ``compute_tod_rvol``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from sentinel.engine.rvol import (
    FROZEN_CURVES,
    UCurve,
    _curve_at,
    _mins_from_open,
)

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "sentinel.db"
DEFAULT_PROFILE_PATH = ROOT / "data" / "journal" / "rvol_baseline.json"

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
INST_IDS = {
    "NIFTY": "NIFTY-FUT",
    "BANKNIFTY": "BANKNIFTY-FUT",
    "SENSEX": "SENSEX-FUT",
}

# Rolling window and acceptance band — fail loud, keep prior/frozen on reject.
LOOKBACK_SESSIONS = 12
MIN_SESSIONS = 4
MIN_BARS_PER_DAY = 300  # near-complete 09:15–15:30 tape (~376 expected)
# Fraction of in-session bars with vol/baseline ≥ 1.5 under the candidate curve.
RVOL15_MIN = 0.05
RVOL15_MAX = 0.35
OPEN_TO_TROUGH_MIN = 1.5  # open peak must dominate midday
PROFILE_SCHEMA = 1
PROFILE_MAX_AGE_DAYS = 14  # hot-load refuses older live profiles → frozen

_write_lock = threading.Lock()


@dataclass(frozen=True)
class FitReport:
    ok: bool
    reason: str
    sessions: tuple[str, ...]
    curves: dict[str, UCurve]
    prevalence: dict[str, float]
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "sessions": list(self.sessions),
            "curves": {k: dict(v._asdict()) for k, v in self.curves.items()},
            "prevalence": dict(self.prevalence),
            "path": self.path,
        }


def _median_or_none(vals: list[float]) -> float | None:
    clean = [float(v) for v in vals if v is not None and float(v) > 0]
    if not clean:
        return None
    return float(median(clean))


def list_complete_sessions(
    db_path: Path,
    *,
    as_of: date | None = None,
    lookback: int = LOOKBACK_SESSIONS,
    min_bars: int = MIN_BARS_PER_DAY,
) -> list[str]:
    """Newest-first complete session dates (ISO) with enough Nifty 1m bars."""
    as_of = as_of or datetime.now(_IST).date()
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            """
            SELECT substr(start_ts, 1, 10) AS d, COUNT(*) AS n
            FROM sentinel_bars
            WHERE instrument_id = 'NIFTY-FUT' AND timeframe = '1m'
            GROUP BY d
            HAVING n >= ?
            ORDER BY d DESC
            """,
            (int(min_bars),),
        ).fetchall()
    finally:
        con.close()
    out: list[str] = []
    for d, _n in rows:
        try:
            day = date.fromisoformat(str(d))
        except ValueError:
            continue
        if day > as_of:
            continue
        out.append(day.isoformat())
        if len(out) >= int(lookback):
            break
    return out


def _load_volumes(
    db_path: Path,
    sessions: Iterable[str],
) -> dict[tuple[str, str], list[float]]:
    """(index_key, HH:MM) → volumes across the listed sessions."""
    sessions = list(sessions)
    if not sessions:
        return {}
    placeholders = ",".join("?" * len(sessions))
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    by: dict[tuple[str, str], list[float]] = defaultdict(list)
    try:
        for key, inst in INST_IDS.items():
            rows = con.execute(
                f"""
                SELECT start_ts, volume_units
                FROM sentinel_bars
                WHERE instrument_id = ? AND timeframe = '1m'
                  AND substr(start_ts, 1, 10) IN ({placeholders})
                """,
                (inst, *sessions),
            ).fetchall()
            for ts, vol in rows:
                hhmm = str(ts)[11:16]
                if not ("09:15" <= hhmm <= "15:30"):
                    continue
                v = float(vol or 0)
                if v <= 0:
                    continue
                by[(key, hhmm)].append(v)
    finally:
        con.close()
    return by


def _slot_median(by: dict[tuple[str, str], list[float]], key: str, start: str, end: str) -> float | None:
    vals: list[float] = []
    for (k, hhmm), vols in by.items():
        if k != key:
            continue
        if start <= hhmm <= end:
            m = _median_or_none(vols)
            if m is not None:
                vals.append(m)
    return _median_or_none(vals)


def fit_curves_from_volumes(by: dict[tuple[str, str], list[float]]) -> dict[str, UCurve]:
    """Fit UCurve anchors from aggregated minute volumes."""
    curves: dict[str, UCurve] = {}
    for key in INDICES:
        open_peak = _median_or_none(by.get((key, "09:15"), []))
        if open_peak is None:
            open_peak = _slot_median(by, key, "09:15", "09:20")
        trough = _slot_median(by, key, "11:45", "12:45")
        close_peak = _median_or_none(by.get((key, "15:15"), []))
        if close_peak is None:
            close_peak = _slot_median(by, key, "15:00", "15:25")
        if open_peak is None or trough is None or close_peak is None:
            raise ValueError(f"insufficient volume anchors for {key}")
        if open_peak < OPEN_TO_TROUGH_MIN * trough:
            raise ValueError(
                f"{key} open_peak {open_peak:.0f} < {OPEN_TO_TROUGH_MIN}× trough {trough:.0f}"
            )
        floor = max(trough * 0.5, 1.0)
        curves[key] = UCurve(
            open_peak=float(open_peak),
            trough=float(trough),
            close_peak=float(close_peak),
            floor=float(floor),
        )
    return curves


def prevalence_rvol15(
    by: dict[tuple[str, str], list[float]],
    curves: dict[str, UCurve],
) -> dict[str, float]:
    """Share of (index, minute) volume prints with vol/baseline ≥ 1.5."""
    out: dict[str, float] = {}
    for key in INDICES:
        curve = curves[key]
        ge = tot = 0
        for (k, hhmm), vols in by.items():
            if k != key:
                continue
            base = _curve_at(_mins_from_open(hhmm), curve)
            if base <= 0:
                continue
            for v in vols:
                tot += 1
                if v / base >= 1.5:
                    ge += 1
        out[key] = (ge / tot) if tot else 0.0
    return out


def validate_curves(
    curves: dict[str, UCurve],
    prevalence: dict[str, float],
) -> tuple[bool, str]:
    for key in INDICES:
        if key not in curves:
            return False, f"missing curve {key}"
        c = curves[key]
        if c.trough <= 0 or c.open_peak <= 0 or c.close_peak <= 0 or c.floor <= 0:
            return False, f"{key} non-positive anchor"
        if c.open_peak < OPEN_TO_TROUGH_MIN * c.trough:
            return False, f"{key} open/trough ratio too small"
        p = float(prevalence.get(key, 0.0))
        if p < RVOL15_MIN or p > RVOL15_MAX:
            return False, (
                f"{key} RVol≥1.5 prevalence {p:.1%} outside "
                f"[{RVOL15_MIN:.0%}, {RVOL15_MAX:.0%}] — refusing fit"
            )
    return True, "ok"


def fit_from_db(
    db_path: Path | None = None,
    *,
    as_of: date | None = None,
    lookback: int = LOOKBACK_SESSIONS,
    min_sessions: int = MIN_SESSIONS,
) -> FitReport:
    """Fit + validate curves from sentinel.db. Does not write."""
    db_path = Path(db_path) if db_path is not None else DEFAULT_DB
    if not db_path.exists():
        return FitReport(False, f"missing db {db_path}", (), dict(FROZEN_CURVES), {})
    sessions = list_complete_sessions(db_path, as_of=as_of, lookback=lookback)
    if len(sessions) < min_sessions:
        return FitReport(
            False,
            f"need ≥{min_sessions} complete sessions, have {len(sessions)}",
            tuple(sorted(sessions)),
            dict(FROZEN_CURVES),
            {},
        )
    # Fit on chronological order (oldest→newest) for stable medians
    sessions_asc = tuple(sorted(sessions))
    by = _load_volumes(db_path, sessions_asc)
    try:
        curves = fit_curves_from_volumes(by)
    except ValueError as exc:
        return FitReport(False, str(exc), sessions_asc, dict(FROZEN_CURVES), {})
    prev = prevalence_rvol15(by, curves)
    ok, reason = validate_curves(curves, prev)
    return FitReport(ok, reason, sessions_asc, curves, prev)


def profile_payload(report: FitReport, *, fitted_at: datetime | None = None) -> dict[str, Any]:
    fitted_at = fitted_at or datetime.now(_IST)
    return {
        "schema": PROFILE_SCHEMA,
        "fitted_at": fitted_at.isoformat(),
        "fit_sessions": list(report.sessions),
        "prevalence_rvol15": {k: round(v, 4) for k, v in report.prevalence.items()},
        "curves": {k: dict(v._asdict()) for k, v in report.curves.items()},
        "notes": (
            "Empirical asymmetric U-curve for compute_tod_rvol. "
            "Classifier CONT gates unchanged. Rejected fits keep prior/frozen."
        ),
    }


def write_profile(payload: dict[str, Any], path: Path | None = None) -> Path:
    path = Path(path) if path is not None else DEFAULT_PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, indent=2, sort_keys=True)
    with _write_lock:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    return path


def read_profile(path: Path | None = None) -> dict[str, Any] | None:
    path = Path(path) if path is not None else DEFAULT_PROFILE_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("rvol baseline profile unreadable (%s): %s", path, exc)
        return None


def curves_from_payload(payload: dict[str, Any]) -> dict[str, UCurve] | None:
    """Parse curves; shape-check only (prevalence was enforced at write time)."""
    raw = payload.get("curves") or {}
    out: dict[str, UCurve] = {}
    try:
        for key in INDICES:
            c = raw[key]
            out[key] = UCurve(
                open_peak=float(c["open_peak"]),
                trough=float(c["trough"]),
                close_peak=float(c["close_peak"]),
                floor=float(c["floor"]),
            )
    except Exception as exc:
        log.warning("rvol baseline curves invalid: %s", exc)
        return None
    for key, c in out.items():
        if c.trough <= 0 or c.open_peak <= 0 or c.close_peak <= 0 or c.floor <= 0:
            log.warning("rvol baseline rejected on load: %s non-positive anchor", key)
            return None
        if c.open_peak < OPEN_TO_TROUGH_MIN * c.trough:
            log.warning("rvol baseline rejected on load: %s open/trough too small", key)
            return None
    return out


def profile_is_fresh(payload: dict[str, Any], *, now: datetime | None = None) -> bool:
    now = now or datetime.now(_IST)
    raw = payload.get("fitted_at")
    if not raw:
        return False
    try:
        fitted = datetime.fromisoformat(str(raw))
        if fitted.tzinfo is None:
            fitted = fitted.replace(tzinfo=_IST)
    except ValueError:
        return False
    age = now.astimezone(_IST) - fitted.astimezone(_IST)
    return age.days <= PROFILE_MAX_AGE_DAYS


def refit_and_persist(
    db_path: Path | None = None,
    profile_path: Path | None = None,
    *,
    as_of: date | None = None,
) -> FitReport:
    """Fit, validate, write profile. On reject, leave prior file untouched."""
    report = fit_from_db(db_path, as_of=as_of)
    if not report.ok:
        log.warning("rvol baseline refit refused: %s", report.reason)
        return report
    path = write_profile(profile_payload(report), path=profile_path)
    log.info(
        "rvol baseline refit ok → %s sessions=%s prevalence=%s",
        path,
        list(report.sessions),
        {k: round(v, 3) for k, v in report.prevalence.items()},
    )
    # Force hot-reload on next baseline read.
    try:
        from sentinel.engine import rvol as rvol_mod

        rvol_mod.invalidate_live_curves()
    except Exception:
        pass
    return FitReport(
        True,
        report.reason,
        report.sessions,
        report.curves,
        report.prevalence,
        path=str(path),
    )
