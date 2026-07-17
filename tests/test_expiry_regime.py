"""Hand-checks for the expiry-structure era classifier (the two SEBI/NSE breaks)."""

from datetime import date, datetime

from app.expiry_regime import ERAS, era_order, expiry_era


def test_era_boundaries_are_half_open():
    # pre-Nov24 up to (not incl.) 2024-11-20
    assert expiry_era("2024-06-01") == "pre-Nov24"
    assert expiry_era("2024-11-19") == "pre-Nov24"
    # 20 Nov 2024 starts the single-weekly era
    assert expiry_era("2024-11-20") == "Nov24-Aug25"
    assert expiry_era("2025-08-31") == "Nov24-Aug25"
    # 1 Sep 2025 starts the Tuesday-weekly era
    assert expiry_era("2025-09-01") == "Sep25+"
    assert expiry_era("2026-07-15") == "Sep25+"


def test_accepts_date_and_datetime():
    assert expiry_era(date(2024, 11, 20)) == "Nov24-Aug25"
    assert expiry_era(datetime(2025, 9, 1, 13, 45)) == "Sep25+"


def test_era_order_is_chronological_and_filters():
    assert era_order() == list(ERAS)
    assert era_order({"Sep25+", "pre-Nov24"}) == ["pre-Nov24", "Sep25+"]
