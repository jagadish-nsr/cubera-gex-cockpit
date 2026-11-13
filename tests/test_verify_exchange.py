"""Lot / holiday VERIFY drill (Pillar E4)."""

from __future__ import annotations

from datetime import date

from app.verify_exchange import EXPECTED_LOTS, build_verify_drill


def test_verify_drill_passes_with_current_lots():
    payload = build_verify_drill(as_of=date(2026, 11, 13))
    assert payload["expected_lots"] == EXPECTED_LOTS
    assert payload["pass"] is True
    assert payload["failed"] == []
    ids = {c["id"] for c in payload["checks"]}
    assert "lot_nifty" in ids
    assert "lot_banknifty" in ids
    assert "lot_sensex" in ids
    assert "holiday_year_2026" in ids
    assert "index_key_banknifty" in ids


def test_verify_drill_december_eager_next_year():
    payload = build_verify_drill(as_of=date(2026, 12, 15))
    ids = {c["id"] for c in payload["checks"]}
    assert "holiday_year_2027_dec_eager" in ids
