"""Tests for the Dhan API adapter (data/dhan.py).

All network calls are mocked — these tests run fully offline and do not require
Dhan credentials. They verify the parsing and transformation logic only.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.dhan import (
    DhanAPIError,
    DhanCredentialError,
    _parse_dhan_expiry,
    aggregate_oi_volume_dhan,
    nearest_expiry_premiums_dhan,
    parse_dhan_option_chain,
    per_strike_oi_dhan,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FETCHED_AT = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)

_SAMPLE_RAW = {
    "data": {
        "underlying_scrip_price": "24050.75",
        "oc": {
            "24000": {
                "ce": {
                    "open_interest": 10000,
                    "change_in_oi": 500,
                    "volume": 25000,
                    "bid_price": 150.0,
                    "ask_price": 151.0,
                    "last_price": 150.5,
                },
                "pe": {
                    "open_interest": 8000,
                    "change_in_oi": 200,
                    "volume": 12000,
                    "bid_price": 100.0,
                    "ask_price": 100.5,
                    "last_price": 100.25,
                },
            },
            "24500": {
                "ce": {
                    "open_interest": 5000,
                    "change_in_oi": 100,
                    "volume": 8000,
                    "bid_price": 49.75,
                    "ask_price": 50.25,
                    "last_price": 50.0,
                },
                "pe": None,
            },
        },
    }
}

_EXPIRY = date(2026, 7, 10)


# ---------------------------------------------------------------------------
# _parse_dhan_expiry
# ---------------------------------------------------------------------------

class TestParseDhanExpiry:
    def test_iso_format(self):
        assert _parse_dhan_expiry("2026-07-31") == date(2026, 7, 31)

    def test_iso_with_extra_chars(self):
        # Dhan may return full datetime strings; we only use the date part
        assert _parse_dhan_expiry("2026-07-31T00:00:00") == date(2026, 7, 31)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Unrecognised"):
            _parse_dhan_expiry("31/07/2026")


# ---------------------------------------------------------------------------
# parse_dhan_option_chain
# ---------------------------------------------------------------------------

class TestParseDhanOptionChain:
    def test_happy_path(self):
        rows, spot = parse_dhan_option_chain(_SAMPLE_RAW, "NIFTY", _EXPIRY, _FETCHED_AT)
        assert len(rows) == 3  # 2 (24000 CE+PE) + 1 (24500 CE only)
        assert spot == pytest.approx(24050.75)

    def test_types_normalised(self):
        rows, _ = parse_dhan_option_chain(_SAMPLE_RAW, "NIFTY", _EXPIRY, _FETCHED_AT)
        types = {r["type"] for r in rows}
        assert types == {"C", "P"}

    def test_mid_from_bid_ask(self):
        rows, _ = parse_dhan_option_chain(_SAMPLE_RAW, "NIFTY", _EXPIRY, _FETCHED_AT)
        call_24k = next(r for r in rows if r["type"] == "C" and r["strike"] == 24000.0)
        assert call_24k["price"] == pytest.approx(150.5)  # (150 + 151) / 2

    def test_iv_is_nan(self):
        import math
        rows, _ = parse_dhan_option_chain(_SAMPLE_RAW, "NIFTY", _EXPIRY, _FETCHED_AT)
        assert all(math.isnan(r["iv"]) for r in rows)

    def test_none_leg_skipped(self):
        # 24500 pe is None — should only produce 1 row for 24500
        rows, _ = parse_dhan_option_chain(_SAMPLE_RAW, "NIFTY", _EXPIRY, _FETCHED_AT)
        rows_24500 = [r for r in rows if r["strike"] == 24500.0]
        assert len(rows_24500) == 1
        assert rows_24500[0]["type"] == "C"

    def test_empty_oc_raises(self):
        with pytest.raises(ValueError, match="no data"):
            parse_dhan_option_chain({"data": {"oc": {}}}, "NIFTY", _EXPIRY, _FETCHED_AT)

    def test_missing_underlying_price(self):
        raw = {"data": {"oc": {"24000": {"ce": {"open_interest": 100, "last_price": 50}, "pe": None}}}}
        rows, spot = parse_dhan_option_chain(raw, "NIFTY", _EXPIRY, _FETCHED_AT)
        assert spot is None


# ---------------------------------------------------------------------------
# aggregate_oi_volume_dhan
# ---------------------------------------------------------------------------

class TestAggregateOiVolumeDhan:
    def setup_method(self):
        self.rows, _ = parse_dhan_option_chain(_SAMPLE_RAW, "NIFTY", _EXPIRY, _FETCHED_AT)

    def test_call_put_oi_positive(self):
        agg = aggregate_oi_volume_dhan(self.rows)
        assert agg["call_oi"] > 0
        assert agg["put_oi"] > 0

    def test_pcr_ratio(self):
        agg = aggregate_oi_volume_dhan(self.rows)
        expected = agg["put_oi"] / agg["call_oi"]
        assert agg["pcr_oi"] == pytest.approx(expected)

    def test_sums_correct(self):
        agg = aggregate_oi_volume_dhan(self.rows)
        call_rows = [r for r in self.rows if r["type"] == "C"]
        put_rows  = [r for r in self.rows if r["type"] == "P"]
        assert agg["call_oi"] == pytest.approx(sum(r["oi"] for r in call_rows))
        assert agg["put_oi"]  == pytest.approx(sum(r["oi"] for r in put_rows))


# ---------------------------------------------------------------------------
# per_strike_oi_dhan
# ---------------------------------------------------------------------------

class TestPerStrikeOiDhan:
    def setup_method(self):
        self.rows, self.spot = parse_dhan_option_chain(_SAMPLE_RAW, "NIFTY", _EXPIRY, _FETCHED_AT)

    def test_returns_list(self):
        result = per_strike_oi_dhan(self.rows)
        assert isinstance(result, list)

    def test_sorted_by_strike(self):
        result = per_strike_oi_dhan(self.rows)
        strikes = [s["strike"] for s in result]
        assert strikes == sorted(strikes)

    def test_band_filter(self):
        # Only 24000 is within 100pts of spot 24050.75
        result = per_strike_oi_dhan(self.rows, spot=24050.75, band_pts=100)
        strikes = [s["strike"] for s in result]
        assert 24000.0 in strikes
        assert 24500.0 not in strikes

    def test_no_filter_returns_all(self):
        result = per_strike_oi_dhan(self.rows)
        assert len(result) == 2  # 24000 and 24500


# ---------------------------------------------------------------------------
# nearest_expiry_premiums_dhan
# ---------------------------------------------------------------------------

class TestNearestExpiryPremiumsDhan:
    def setup_method(self):
        self.rows, _ = parse_dhan_option_chain(_SAMPLE_RAW, "NIFTY", _EXPIRY, _FETCHED_AT)

    def test_returns_correct_structure(self):
        prem = nearest_expiry_premiums_dhan(self.rows)
        assert 24000.0 in prem
        assert "call_ltp" in prem[24000.0]
        assert "put_ltp" in prem[24000.0]

    def test_call_put_ltps_present(self):
        prem = nearest_expiry_premiums_dhan(self.rows)
        assert prem[24000.0]["call_ltp"] is not None
        assert prem[24000.0]["put_ltp"] is not None

    def test_empty_rows(self):
        assert nearest_expiry_premiums_dhan([]) == {}


# ---------------------------------------------------------------------------
# Schema compliance: rows must work with from_nse_results
# ---------------------------------------------------------------------------

class TestRowSchemaCompliance:
    """Verify Dhan rows pass through from_nse_results without error."""

    def test_from_nse_results_accepts_dhan_rows(self):
        from data.nse import from_nse_results
        rows, spot = parse_dhan_option_chain(_SAMPLE_RAW, "NIFTY", _EXPIRY, _FETCHED_AT)
        snap = from_nse_results(
            rows, spot=spot, expiry_window="all",
            dealer_sign_model="short_call_short_put",
            source="dhan", fetched_at=_FETCHED_AT,
        )
        assert not snap.contracts.empty
        assert snap.spot == pytest.approx(24050.75)
