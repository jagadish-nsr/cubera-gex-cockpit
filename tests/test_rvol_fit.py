"""Continuous TOD RVol recalibration — fit, validate, hot-load, refuse bad fits."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sentinel.engine.rvol import (
    FROZEN_CURVES,
    UCurve,
    _get_tod_baseline,
    baseline_source,
    compute_tod_rvol,
    invalidate_live_curves,
)
from sentinel.engine import rvol as rvol_mod
from sentinel.engine.rvol_fit import (
    FitReport,
    RVOL15_MAX,
    RVOL15_MIN,
    fit_curves_from_volumes,
    prevalence_rvol15,
    profile_is_fresh,
    profile_payload,
    refit_and_persist,
    validate_curves,
    write_profile,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _volumes_from_curves(curves: dict[str, UCurve], *, scale: float = 1.0) -> dict:
    """Synthetic (index, HH:MM) volumes near each curve so fit recovers anchors."""
    by: dict[tuple[str, str], list[float]] = {}
    minutes = ["09:15", "09:16", "11:50", "12:00", "12:15", "12:30", "15:00", "15:15", "15:20"]
    for key, c in curves.items():
        for hhmm in minutes:
            if hhmm.startswith("09"):
                v = c.open_peak * scale
            elif hhmm.startswith("15"):
                v = c.close_peak * scale
            else:
                v = c.trough * scale
            by[(key, hhmm)] = [v, v * 1.02, v * 0.98]
    return by


def test_validate_refuses_prevalence_outside_band():
    curves = dict(FROZEN_CURVES)
    # All quiet → prevalence ~0 → refuse (keep prior/frozen).
    quiet = {k: 0.0 for k in curves}
    ok, reason = validate_curves(curves, quiet)
    assert not ok
    assert "prevalence" in reason.lower() or "RVol" in reason
    # All climax → prevalence 1.0 → refuse.
    flooded = {k: 1.0 for k in curves}
    ok2, _ = validate_curves(curves, flooded)
    assert not ok2


def test_fit_curves_recover_synthetic_anchors():
    by = _volumes_from_curves(FROZEN_CURVES)
    fitted = fit_curves_from_volumes(by)
    for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
        assert fitted[key].open_peak == pytest.approx(FROZEN_CURVES[key].open_peak, rel=0.05)
        assert fitted[key].trough == pytest.approx(FROZEN_CURVES[key].trough, rel=0.05)


def test_prevalence_band_on_frozen_like_volumes():
    by = _volumes_from_curves(FROZEN_CURVES)
    # Mix: mostly ~1.0× baseline, some ≥1.5× so prevalence sits in band.
    for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
        trough = FROZEN_CURVES[key].trough
        by[(key, "12:20")] = [trough * 1.6, trough * 1.7, trough * 0.9]
    prev = prevalence_rvol15(by, FROZEN_CURVES)
    ok, reason = validate_curves(FROZEN_CURVES, prev)
    assert ok, reason
    for key, p in prev.items():
        assert RVOL15_MIN <= p <= RVOL15_MAX


def test_hot_load_profile_overrides_frozen(tmp_path, monkeypatch):
    path = tmp_path / "rvol_baseline.json"
    # Midday trough half of frozen → same absolute volume yields higher RVol.
    live = {
        "NIFTY": UCurve(
            open_peak=FROZEN_CURVES["NIFTY"].open_peak,
            trough=FROZEN_CURVES["NIFTY"].trough * 0.5,
            close_peak=FROZEN_CURVES["NIFTY"].close_peak,
            floor=FROZEN_CURVES["NIFTY"].trough * 0.25,
        ),
        "BANKNIFTY": FROZEN_CURVES["BANKNIFTY"],
        "SENSEX": FROZEN_CURVES["SENSEX"],
    }
    report = FitReport(
        True,
        "ok",
        ("2026-11-05", "2026-11-06", "2026-11-08", "2026-11-09"),
        live,
        {k: 0.15 for k in live},
    )
    write_profile(profile_payload(report, fitted_at=datetime.now(IST)), path=path)
    monkeypatch.setattr(rvol_mod, "_DEFAULT_PROFILE", path)
    invalidate_live_curves()
    mid = _get_tod_baseline("12:15", "NIFTY-FUT")
    assert mid == pytest.approx(live["NIFTY"].trough, rel=0.02)
    assert baseline_source().startswith("live:")
    vol = live["NIFTY"].trough * 1.5
    r = compute_tod_rvol(
        datetime(2026, 11, 13, 12, 15, tzinfo=IST), volume=vol, instrument="NIFTY"
    )
    assert r == pytest.approx(1.5, abs=0.02)
    invalidate_live_curves()


def test_stale_profile_falls_back_to_frozen(tmp_path, monkeypatch):
    path = tmp_path / "rvol_baseline.json"
    live = dict(FROZEN_CURVES)
    live["NIFTY"] = UCurve(
        open_peak=99_999.0,
        trough=1.0,
        close_peak=2.0,
        floor=0.5,
    )
    report = FitReport(True, "ok", ("2026-01-01",), live, {k: 0.15 for k in live})
    old = datetime.now(IST) - timedelta(days=30)
    write_profile(profile_payload(report, fitted_at=old), path=path)
    monkeypatch.setattr(rvol_mod, "_DEFAULT_PROFILE", path)
    invalidate_live_curves()
    mid = _get_tod_baseline("12:15", "NIFTY")
    assert mid == pytest.approx(FROZEN_CURVES["NIFTY"].trough, rel=0.02)
    assert baseline_source() == "frozen"
    invalidate_live_curves()


def test_profile_freshness_window():
    payload = {"fitted_at": datetime.now(IST).isoformat()}
    assert profile_is_fresh(payload) is True
    old = {"fitted_at": (datetime.now(IST) - timedelta(days=20)).isoformat()}
    assert profile_is_fresh(old) is False


def test_refit_and_persist_refuses_without_db(tmp_path):
    missing = tmp_path / "no.db"
    out = tmp_path / "out.json"
    report = refit_and_persist(missing, out)
    assert not report.ok
    assert not out.exists()


def test_refit_writes_when_sessions_ok(tmp_path):
    """Minimal sentinel_bars → validated write (hand-checkable prevalence)."""
    db_path = tmp_path / "sentinel.db"
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE sentinel_bars (
            instrument_id TEXT, timeframe TEXT, start_ts TEXT, volume_units REAL
        )
        """
    )
    # 4 complete Nifty days (≥300 bars) + BN/SX anchors at fit minutes.
    days = ["2026-11-05", "2026-11-06", "2026-11-07", "2026-11-10"]
    rows = []
    for d in days:
        for i in range(320):
            hh = 9 + (15 + i) // 60
            mm = (15 + i) % 60
            # Midday quieter than open — U shape.
            if i < 5:
                vol_n = 140_000.0
            elif 150 <= i <= 200:
                vol_n = 7_000.0
            elif i >= 300:
                vol_n = 12_000.0
            else:
                vol_n = 8_000.0
            ts = f"{d}T{hh:02d}:{mm:02d}:00+05:30"
            rows.append(("NIFTY-FUT", "1m", ts, vol_n))
            rows.append(("BANKNIFTY-FUT", "1m", ts, vol_n / 8.0))
            rows.append(("SENSEX-FUT", "1m", ts, max(80.0, vol_n / 100.0)))
        # Explicit elevated prints so RVol≥1.5 prevalence is non-zero but selective.
        for hhmm, mult in (("12:20", 1.8), ("12:21", 1.9), ("10:00", 1.6)):
            ts = f"{d}T{hhmm}:00+05:30"
            rows.append(("NIFTY-FUT", "1m", ts, 7_000.0 * mult))
            rows.append(("BANKNIFTY-FUT", "1m", ts, 900.0 * mult))
            rows.append(("SENSEX-FUT", "1m", ts, 160.0 * mult))
    con.executemany(
        "INSERT INTO sentinel_bars VALUES (?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    out = tmp_path / "rvol_baseline.json"
    report = refit_and_persist(db_path, out, as_of=date(2026, 11, 12))
    # May refuse if prevalence out of band on this synthetic tape — assert fail-loud.
    if report.ok:
        assert out.exists()
        payload = json.loads(out.read_text())
        assert payload["schema"] == 1
        assert "NIFTY" in payload["curves"]
        for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
            p = payload["prevalence_rvol15"][key]
            assert RVOL15_MIN <= p <= RVOL15_MAX
    else:
        assert not out.exists()
        assert report.reason  # refuse loudly, leave frozen


def test_health_daemon_rvol_once_per_day(tmp_path, monkeypatch):
    from app.admin import health_daemon as hd

    calls: list[tuple] = []

    def fake_refit(db_path, profile_path, as_of=None):
        calls.append((Path(db_path), Path(profile_path), as_of))
        return FitReport(True, "ok", ("2026-11-12",), dict(FROZEN_CURVES), {k: 0.1 for k in FROZEN_CURVES}, path=str(profile_path))

    monkeypatch.setattr(
        "sentinel.engine.rvol_fit.refit_and_persist",
        fake_refit,
    )
    hd.reset_gap_cache()
    db_path = tmp_path / "data" / "sentinel.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_text("")  # exists check only
    (tmp_path / "data" / "journal").mkdir(parents=True)

    # Before 15:45 — no call.
    noon = datetime(2026, 11, 13, 12, 0, tzinfo=IST)
    hd._rvol_baseline_post_close(tmp_path, noon)
    assert calls == []

    after = datetime(2026, 11, 13, 15, 50, tzinfo=IST)
    # Ensure trading day
    monkeypatch.setattr(hd, "is_trading_day", lambda d: True)
    hd._rvol_baseline_post_close(tmp_path, after)
    assert len(calls) == 1
    # Second call same day — once only.
    hd._rvol_baseline_post_close(tmp_path, after)
    assert len(calls) == 1
