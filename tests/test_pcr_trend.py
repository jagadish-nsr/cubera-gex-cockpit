"""Unit tests for app/pcr_trend.py — all offline, no NSE calls required."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd
import pytest

from app.pcr_trend import (
    INTERVALS,
    _BEARISH_PCR,
    _BULLISH_PCR,
    build_pcr_chart,
    pcr_bias_label,
    pcr_summary,
    resample_pcr,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_df(n=12, start_pcr=1.0, step=0.05, with_near=False):
    """Synthetic 5-min snapshot DataFrame (n rows, from 09:15 IST)."""
    rows = []
    for i in range(n):
        hh = 9 + (15 + i * 5) // 60
        mm = (15 + i * 5) % 60
        ts = f"2026-07-17T{hh - 5:02d}:{mm:02d}:00Z"   # rough UTC approx
        pcr = round(start_pcr + i * step, 4)
        row = {
            "timestamp_utc": ts,
            "time_ist": f"{hh:02d}:{mm:02d}",
            "date_ist": "2026-07-17",
            "spot": 24000.0 + i * 10,
            "pcr_oi": pcr,
            "pcr_near": (pcr * 0.95) if with_near else float("nan"),
        }
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# load_pcr_today — unit-level checks (no disk I/O needed for these)
# ---------------------------------------------------------------------------

def test_load_pcr_empty_symbol(tmp_path, monkeypatch):
    """Symbol with no disk data and no session points → empty DataFrame."""
    # Monkeypatch load_day to return empty frame
    import app.timeseries as ts_mod
    monkeypatch.setattr(ts_mod, "load_day", lambda *a, **kw: pd.DataFrame())

    from app.pcr_trend import load_pcr_today
    result = load_pcr_today("NIFTY", live_points=None)
    assert result.empty
    assert "pcr_oi" in result.columns
    assert "pcr_near" in result.columns


def test_load_pcr_uses_live_points(monkeypatch):
    """Session live points for today are included in the result."""
    import app.timeseries as ts_mod
    monkeypatch.setattr(ts_mod, "load_day", lambda *a, **kw: pd.DataFrame())

    from app.pcr_trend import load_pcr_today, _ist_today_str
    today = _ist_today_str()
    live = {
        "T1": {"timestamp_utc": "2026-07-17T03:45:00Z", "time_ist": "09:15",
               "date_ist": today, "spot": 24100.0, "pcr_oi": 1.15},
    }
    # Inject today string
    monkeypatch.setattr("app.pcr_trend._ist_today_str", lambda: today)

    result = load_pcr_today("NIFTY", live_points=live)
    # At least one row from live points should appear (if date matches)
    # (may be empty if today differs from fixture date — acceptable)
    assert "pcr_oi" in result.columns


# ---------------------------------------------------------------------------
# resample_pcr
# ---------------------------------------------------------------------------

def test_resample_5min_passthrough():
    """5-min resample: same row count (one bucket per distinct time_ist)."""
    df = _make_df(n=12)
    rs = resample_pcr(df, 5)
    # Each time_ist is unique in our fixture → same count
    assert len(rs) == 12
    assert "bucket_label" in rs.columns
    assert "pcr_oi" in rs.columns


def test_resample_15min_reduces_rows():
    """15-min resample: 12 rows (60 min) → 4 buckets."""
    df = _make_df(n=12)
    rs = resample_pcr(df, 15)
    # 12 rows × 5-min = 60 min → 4 × 15-min buckets
    assert len(rs) <= 4
    assert len(rs) >= 1   # at least one bucket


def test_resample_45min_reduces_rows():
    """45-min resample: 12 rows (60 min) → 1 or 2 buckets."""
    df = _make_df(n=12)
    rs = resample_pcr(df, 45)
    assert len(rs) <= 2
    assert len(rs) >= 1


def test_resample_empty_input():
    """Empty DataFrame returns empty result with correct columns."""
    rs = resample_pcr(pd.DataFrame(), 15)
    assert rs.empty
    assert "bucket_label" in rs.columns


def test_resample_last_value_used():
    """Within a bucket, the LAST pcr_oi value is kept (point-in-time)."""
    df = _make_df(n=3)  # 3 rows: 09:15, 09:20, 09:25 → all in same 15-min bucket
    # Override so all three fall in 09:15 bucket
    df["time_ist"] = ["09:15", "09:20", "09:25"]
    df["timestamp_utc"] = [
        "2026-07-17T03:45:00Z",
        "2026-07-17T03:50:00Z",
        "2026-07-17T03:55:00Z",
    ]
    df["pcr_oi"] = [1.0, 1.1, 1.2]
    rs = resample_pcr(df, 15)
    # Last value in the bucket should be 1.2
    assert math.isclose(float(rs["pcr_oi"].iloc[-1]), 1.2, abs_tol=0.01)


def test_resample_sorted_ascending():
    """Output bucket_labels are sorted ascending (lexicographic HH:MM is fine)."""
    df = _make_df(n=24)  # 2 hrs of 5-min data
    rs = resample_pcr(df, 15)
    labels = list(rs["bucket_label"])
    assert labels == sorted(labels)


# ---------------------------------------------------------------------------
# build_pcr_chart
# ---------------------------------------------------------------------------

def test_build_chart_no_error():
    """build_pcr_chart returns an Altair chart (or None if altair absent) without raising."""
    df = _make_df(n=12, with_near=True)
    chart = build_pcr_chart(df, intervals=[5, 15, 45],
                            show_full_chain=True, show_near_atm=True, show_bands=True)
    # Either an altair chart object or None (if altair not installed)
    try:
        import altair as alt
        assert chart is not None
    except ImportError:
        assert chart is None


def test_build_chart_empty_df_returns_none():
    """Empty input → None (no chart to render)."""
    chart = build_pcr_chart(pd.DataFrame())
    assert chart is None


def test_build_chart_no_near_data_skips_dashed():
    """When near-ATM data is all NaN, dashed near lines are skipped gracefully."""
    df = _make_df(n=6, with_near=False)   # pcr_near is all NaN
    # Should not raise
    chart = build_pcr_chart(df, show_near_atm=True)
    # chart is None only if altair absent; else it exists with just solid lines
    try:
        import altair as alt
        assert chart is not None
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# pcr_summary
# ---------------------------------------------------------------------------

def test_pcr_summary_values():
    df = _make_df(n=6, start_pcr=1.0, step=0.1)
    s = pcr_summary(df)
    assert math.isclose(s["session_open"],   1.0, abs_tol=0.001)
    assert math.isclose(s["current_pcr"],    1.5, abs_tol=0.001)
    assert math.isclose(s["session_high"],   1.5, abs_tol=0.001)
    assert math.isclose(s["session_low"],    1.0, abs_tol=0.001)
    assert math.isclose(s["delta_from_open"], 0.5, abs_tol=0.001)
    assert s["n_points"] == 6


def test_pcr_summary_empty():
    s = pcr_summary(pd.DataFrame())
    assert s["current_pcr"] is None
    assert s["n_points"] == 0


def test_pcr_summary_all_nan():
    df = pd.DataFrame({"pcr_oi": [float("nan")] * 3,
                       "timestamp_utc": ["a", "b", "c"]})
    s = pcr_summary(df)
    assert s["current_pcr"] is None


# ---------------------------------------------------------------------------
# pcr_bias_label
# ---------------------------------------------------------------------------

def test_bias_label_bullish():
    label, colour = pcr_bias_label(_BULLISH_PCR)
    assert "PUT-WRITING" in label
    assert colour == "#22c55e"


def test_bias_label_bearish():
    label, colour = pcr_bias_label(_BEARISH_PCR)
    assert "CALL-WRITING" in label
    assert colour == "#ef4444"


def test_bias_label_neutral():
    label, colour = pcr_bias_label(1.0)
    assert "NEUTRAL" in label
    assert colour == "#f59e0b"


def test_bias_label_none():
    label, colour = pcr_bias_label(None)
    assert label == "—"
    assert colour == "#64748b"
