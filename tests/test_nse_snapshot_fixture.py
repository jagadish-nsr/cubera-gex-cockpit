"""Regression test pinning the REAL NSE option-chain-v3 response shape.

Fixture `data/fixtures/nse_v3_sample.json` is a trimmed live capture (4 strikes
around spot, 2026-05-31). Locks the v3 quirks so a future NSE shape change is
caught: expiry inside CE/PE as DD-MM-YYYY, bid/ask = buyPrice1/sellPrice1,
IV in percent, spot at records.underlyingValue.
"""

import json
from datetime import date
from pathlib import Path

from data.nse import parse_nse_option_chain

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "nse_v3_sample.json"


def _rows():
    payload = json.loads(FIXTURE.read_text())
    rows, spot = parse_nse_option_chain(payload)
    return {(r["strike"], r["type"]): r for r in rows}, spot


def test_spot_and_count():
    by, spot = _rows()
    assert spot == 23547.75
    assert len(by) == 8           # 4 strikes x CE/PE


def test_v3_field_mapping():
    by, _ = _rows()

    ce = by[(23550.0, "C")]
    assert ce["expiry"] == date(2026, 6, 30)          # DD-MM-YYYY parsed
    assert ce["oi"] == 422
    assert abs(ce["iv"] - 0.1584) < 1e-9              # 15.84% -> decimal
    assert ce["price"] == (517.6 + 526.1) / 2         # buyPrice1/sellPrice1 mid

    pe = by[(23500.0, "P")]
    assert pe["oi"] == 51773
    assert abs(pe["iv"] - 0.1522) < 1e-9
    assert pe["price"] == (303.6 + 306.85) / 2
