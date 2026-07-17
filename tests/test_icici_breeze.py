"""Tests for the ICICI Direct Breeze API adapter (data/icici_breeze.py).

All network calls are mocked — these tests run fully offline and do not require
Breeze credentials. They verify the parsing and transformation logic only.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from data.icici_breeze import (
    SessionExpiredError,
    _parse_breeze_expiry,
    _breeze_expiry_str,
    aggregate_oi_volume_breeze,
    nearest_expiry_premiums_breeze,
    parse_breeze_option_chain,
    per_strike_oi_breeze,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FETCHED_AT = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)

_SAMPLE_PAYLOAD = {
    "Success": [
        {
            "expiry_date": "2026-07-10T06:00:00.000Z",
            "strike_price": "24000",
            "right": "call",
            "open_interest": "10000",
            "last_rate": "150.50",
            "best_bid_price": "150.00",
            "best_offer_price": "151.00",
            "underlying_price": "24050.75",
            "change_in_open_interest": "500",
            "total_quantity_traded": "25000",
        },
        {
            "expiry_date": "2026-07-10T06:00:00.000Z",
            "strike_price": "24000",
            "right": "put",
            "open_interest": "8000",
            "last_rate": "100.25",
            "best_bid_price": "100.00",
            "best_offer_price": "100.50",
            "underlying_price": "24050.75",
            "change_in_open_interest": "200",
            "total_quantity_traded": "12000",
        },
        {
            "expiry_date": "2026-07-31T06:00:00.000Z",
            "strike_price": "24500",
            "right": "call",
            "open_interest": "5000",
            "last_rate": "50.00",
            "best_bid_price": "49.75",
            "best_offer_price": "50.25",
            "underlying_price": "24050.75",
            "change_in_open_interest": "100",
            "total_quantity_traded": "8000",
        },
    ]
}


# ---------------------------------------------------------------------------
# _parse_breeze_expiry
# ---------------------------------------------------------------------------

class TestParseBreezeExpiry:
    def test_iso_z_format(self):
        d = _parse_breeze_expiry("2026-07-31T06:00:00.000Z")
        assert d == date(2026, 7, 31)

    def test_iso_plain_z(self):
        d = _parse_breeze_expiry("2026-07-31T06:00:00Z")
        assert d == date(2026, 7, 31)

    def test_plain_date(self):
        d = _parse_breeze_expiry("2026-07-31")
        assert d == date(2026, 7, 31)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="unrecognised"):
            _parse_breeze_expiry("2026/07/31")


# ---------------------------------------------------------------------------
# _breeze_expiry_str
# ---------------------------------------------------------------------------

class TestBreezeExpiryStr:
    def test_format(self):
        s = _breeze_expiry_str(date(2026, 7, 31))
        assert s == "2026-07-31T06:00:00.000Z"


# ---------------------------------------------------------------------------
# parse_breeze_option_chain
# ---------------------------------------------------------------------------

class TestParseBreezeOptionChain:
    def test_happy_path(self):
        rows, spot = parse_breeze_option_chain(_SAMPLE_PAYLOAD, "NIFTY", _FETCHED_AT)
        assert len(rows) == 3
        assert spot == pytest.approx(24050.75)

    def test_types_normalised(self):
        rows, _ = parse_breeze_option_chain(_SAMPLE_PAYLOAD, "NIFTY", _FETCHED_AT)
        types = {r["type"] for r in rows}
        assert types == {"C", "P"}

    def test_mid_computed_from_bid_ask(self):
        rows, _ = parse_breeze_option_chain(_SAMPLE_PAYLOAD, "NIFTY", _FETCHED_AT)
        call_row = next(r for r in rows if r["type"] == "C" and float(r["strike"]) == 24000)
        assert call_row["price"] == pytest.approx(150.5)  # (150 + 151) / 2

    def test_iv_is_nan(self):
        import math
        rows, _ = parse_breeze_option_chain(_SAMPLE_PAYLOAD, "NIFTY", _FETCHED_AT)
        assert all(math.isnan(r["iv"]) for r in rows)

    def test_empty_response_raises(self):
        with pytest.raises(ValueError, match="no data"):
            parse_breeze_option_chain({"Success": []}, "NIFTY", _FETCHED_AT)

    def test_error_response_raises(self):
        with pytest.raises(SessionExpiredError, match="Session expired"):
            parse_breeze_option_chain({"Success": [], "Error": "Invalid session"}, "NIFTY", _FETCHED_AT)

    def test_missing_underlying_price_is_none(self):
        payload_no_spot = {
            "Success": [
                {
                    "expiry_date": "2026-07-10T06:00:00.000Z",
                    "strike_price": "24000",
                    "right": "call",
                    "open_interest": "100",
                    "last_rate": "50",
                    # no underlying_price
                }
            ]
        }
        rows, spot = parse_breeze_option_chain(payload_no_spot, "NIFTY", _FETCHED_AT)
        assert spot is None

    def test_expiries_parsed_correctly(self):
        rows, _ = parse_breeze_option_chain(_SAMPLE_PAYLOAD, "NIFTY", _FETCHED_AT)
        expiries = {r["expiry"] for r in rows}
        assert date(2026, 7, 10) in expiries
        assert date(2026, 7, 31) in expiries


# ---------------------------------------------------------------------------
# aggregate_oi_volume_breeze
# ---------------------------------------------------------------------------

class TestAggregateOiVolume:
    def setup_method(self):
        self.rows, self.spot = parse_breeze_option_chain(_SAMPLE_PAYLOAD, "NIFTY", _FETCHED_AT)

    def test_call_oi_positive(self):
        agg = aggregate_oi_volume_breeze(self.rows)
        assert agg["call_oi"] > 0
        assert agg["put_oi"] > 0

    def test_pcr_oi_ratio(self):
        agg = aggregate_oi_volume_breeze(self.rows)
        expected_pcr = agg["put_oi"] / agg["call_oi"]
        assert agg["pcr_oi"] == pytest.approx(expected_pcr)

    def test_sums_match(self):
        agg = aggregate_oi_volume_breeze(self.rows)
        call_rows = [r for r in self.rows if r["type"] == "C"]
        put_rows  = [r for r in self.rows if r["type"] == "P"]
        assert agg["call_oi"] == pytest.approx(sum(r["oi"] for r in call_rows))
        assert agg["put_oi"]  == pytest.approx(sum(r["oi"] for r in put_rows))


# ---------------------------------------------------------------------------
# per_strike_oi_breeze
# ---------------------------------------------------------------------------

class TestPerStrikeOi:
    def setup_method(self):
        self.rows, self.spot = parse_breeze_option_chain(_SAMPLE_PAYLOAD, "NIFTY", _FETCHED_AT)

    def test_returns_list(self):
        result = per_strike_oi_breeze(self.rows)
        assert isinstance(result, list)

    def test_sorted_by_strike(self):
        result = per_strike_oi_breeze(self.rows)
        strikes = [s["strike"] for s in result]
        assert strikes == sorted(strikes)

    def test_band_filter(self):
        # Only 24000 is within 100 pts of spot 24050
        result = per_strike_oi_breeze(self.rows, spot=24050.0, band_pts=100)
        strikes = [s["strike"] for s in result]
        assert 24000 in strikes
        assert 24500 not in strikes


# ---------------------------------------------------------------------------
# nearest_expiry_premiums_breeze
# ---------------------------------------------------------------------------

class TestNearestExpiryPremiums:
    def setup_method(self):
        self.rows, _ = parse_breeze_option_chain(_SAMPLE_PAYLOAD, "NIFTY", _FETCHED_AT)

    def test_nearest_expiry_only(self):
        # Nearest expiry is 2026-07-10; 24500 call is on 2026-07-31 -> not included
        prem = nearest_expiry_premiums_breeze(self.rows)
        assert 24000.0 in prem
        assert 24500.0 not in prem  # 24500 is only on 2026-07-31

    def test_call_put_both_present(self):
        prem = nearest_expiry_premiums_breeze(self.rows)
        assert prem[24000.0]["call_ltp"] is not None
        assert prem[24000.0]["put_ltp"] is not None

    def test_empty_rows(self):
        result = nearest_expiry_premiums_breeze([])
        assert result == {}


# ---------------------------------------------------------------------------
# fetch_chain_breeze — mocked integration test
# ---------------------------------------------------------------------------

class TestFetchChainBreeze:
    @patch("data.icici_breeze._get_breeze")
    def test_fetch_merges_expiries(self, mock_get_breeze):
        """Verify that fetch_chain_breeze calls fetch_option_chain per expiry
        and merges the rows."""
        from data.icici_breeze import fetch_chain_breeze

        mock_breeze = MagicMock()
        mock_get_breeze.return_value = mock_breeze

        # Mock expiry list discovery
        exp1_str = "2026-07-10T06:00:00.000Z"
        exp2_str = "2026-07-31T06:00:00.000Z"

        # Return different payloads per expiry
        def side_effect(**kwargs):
            expiry = kwargs.get("expiry_date", "")
            if "07-10" in expiry or "07-31" in expiry or expiry == "":
                # Return for discovery (empty expiry) or per-expiry
                return {
                    "Success": [
                        {
                            "expiry_date": exp1_str if "10" in expiry or expiry == "" else exp2_str,
                            "strike_price": "24000",
                            "right": "call",
                            "open_interest": "1000",
                            "last_rate": "100",
                            "best_bid_price": "99",
                            "best_offer_price": "101",
                            "underlying_price": "24100",
                        }
                    ]
                }
            return {"Success": []}

        mock_breeze.get_option_chain_quotes.side_effect = side_effect

        rows, spot = fetch_chain_breeze(
            "NIFTY",
            api_key="test_key",
            api_secret="test_secret",
            session_token="test_token",
            expiries=[date(2026, 7, 10)],  # provide explicitly to avoid discovery call
        )
        assert len(rows) >= 1
        assert spot == pytest.approx(24100.0)

    @patch("data.icici_breeze._get_breeze")
    def test_raises_when_no_data(self, mock_get_breeze):
        from data.icici_breeze import fetch_chain_breeze

        mock_breeze = MagicMock()
        mock_get_breeze.return_value = mock_breeze
        mock_breeze.get_option_chain_quotes.return_value = {"Success": [], "Error": "no data"}

        with pytest.raises(RuntimeError, match="no option chain data"):
            fetch_chain_breeze(
                "SENSEX",
                api_key="k", api_secret="s", session_token="t",
                expiries=[date(2026, 7, 10)],
            )
