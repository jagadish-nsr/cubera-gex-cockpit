"""Multi-index plumbing: the Index registry, has_weekly expiry behaviour, the
monthly-only fetch resolution, and per-symbol journal separation.

Hand-checkable facts (verify-live landmines, CLAUDE.md):
  - NIFTY: lot 65 (SEBI revision Jan-2026, was 75), WEEKLY + monthly.
  - BANKNIFTY: lot 30, MONTHLY-ONLY (no weeklies since Nov-2024), last Tuesday.
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import config
import app.timeseries as ts
from data.expiry import (filter_expiry_window, next_monthly_expiry,
                         next_weekly_expiry)
from data.nse import _resolve_expiries


# --- registry ---------------------------------------------------------------

def test_index_registry_values():
    nifty = config.get_index("NIFTY")
    bank = config.get_index("BANKNIFTY")
    assert nifty.lot_size == 65 and nifty.has_weekly is True
    assert bank.lot_size == 30 and bank.has_weekly is False        # monthly-only
    assert bank.band_pts > nifty.band_pts                          # bigger index, wider band
    sensex = config.get_index("SENSEX")
    assert sensex.lot_size == 20 and sensex.has_weekly is True      # BSE, weekly (Thu)
    assert sensex.regime_proxy == "NIFTY"                          # borrows NIFTY's vanna regime
    assert nifty.regime_proxy is None and bank.regime_proxy is None  # own regime


def test_get_index_default_and_unknown():
    assert config.get_index(None).symbol == config.DEFAULT_INDEX   # None -> default
    assert config.get_index("banknifty").symbol == "BANKNIFTY"     # case-insensitive
    assert config.get_index("sensex").symbol == "SENSEX"           # BSE index, now supported
    with pytest.raises(ValueError):                                 # fail loud, not silent
        config.get_index("FINNIFTY")


def test_legacy_aliases_track_default_index():
    d = config.get_index(config.DEFAULT_INDEX)
    assert config.CONTRACT_SIZE == d.lot_size
    assert config.UNDERLYING == d.symbol
    assert config.OI_STRIKE_BAND_PTS == d.band_pts


# --- expiry: has_weekly collapses the weekly window -------------------------

def test_weekly_window_collapses_to_monthly_when_no_weeklies():
    ref = date(2026, 6, 1)
    weekly = next_weekly_expiry(ref)
    monthly = next_monthly_expiry(ref)
    assert weekly < monthly                       # there IS a sooner Tuesday
    df = pd.DataFrame({"expiry": [weekly, monthly]})

    # With weeklies (Nifty): the weekly window stops at the next weekly Tuesday.
    kept_w = set(filter_expiry_window(df, "weekly", ref, has_weekly=True)["expiry"])
    assert weekly in kept_w and monthly not in kept_w

    # Monthly-only (Bank Nifty): the weekly window reaches the monthly instead, so
    # it can't silently return an empty chain.
    kept_m = set(filter_expiry_window(df, "weekly", ref, has_weekly=False)["expiry"])
    assert monthly in kept_m


# --- fetch resolution: monthly-only loops just the monthly ------------------

def test_resolve_expiries_monthly_only_is_single():
    monthly_only = _resolve_expiries(None, has_weekly=False)
    assert len(monthly_only) == 1                 # one call, the monthly
    with_weeklies = _resolve_expiries(None, has_weekly=True)
    assert len(with_weeklies) >= 1
    # the monthly-only target is the last (latest) of the weekly list
    assert monthly_only[0] == with_weeklies[-1]


# --- journals: the two indices never share a file ---------------------------

def _strike_data(spot, strike):
    return SimpleNamespace(
        fetched_at=datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc),
        levels=SimpleNamespace(spot=spot),
        oi_strikes=[{"strike": strike, "call_oi": 1, "call_doi": 2, "put_oi": 3,
                     "put_doi": 4, "call_vol": 5, "put_vol": 6,
                     "call_ltp": 10.0, "put_ltp": 11.0, "net_gex": 1}],
    )


def test_per_symbol_strike_journals_are_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "OISTRIKES_DIR", tmp_path)
    ts.append_strike_rows(ts.build_strike_rows(_strike_data(24000.0, 24000.0)),
                          symbol="NIFTY")
    ts.append_strike_rows(ts.build_strike_rows(_strike_data(57000.0, 57000.0)),
                          symbol="BANKNIFTY")

    n = ts.load_strikes_day("2026-06-30", symbol="NIFTY")
    b = ts.load_strikes_day("2026-06-30", symbol="BANKNIFTY")
    assert len(n) == 1 and n["strike"].iloc[0] == 24000.0      # only Nifty's row
    assert len(b) == 1 and b["strike"].iloc[0] == 57000.0      # only Bank Nifty's row
    assert (tmp_path / "NIFTY" / "2026-06-30.csv").exists()
    assert (tmp_path / "BANKNIFTY" / "2026-06-30.csv").exists()


def test_per_symbol_row_journals_and_available_days(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "TS_DIR", tmp_path)
    row = {"date_ist": "2026-06-30", **{f: "" for f in ts.FIELDS if f != "date_ist"}}
    ts.append_row(dict(row), symbol="BANKNIFTY")
    assert ts.available_days(symbol="BANKNIFTY") == ["2026-06-30"]
    assert ts.available_days(symbol="NIFTY") == []             # not cross-contaminated
