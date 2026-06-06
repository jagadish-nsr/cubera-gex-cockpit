"""India VIX parsing from the NSE allIndices payload (hand-checkable)."""

from data.nse import parse_india_vix


def test_parse_india_vix_found():
    payload = {"data": [
        {"index": "NIFTY 50", "indexSymbol": "NIFTY 50", "last": 23500},
        {"index": "INDIA VIX", "indexSymbol": "INDIA VIX", "last": 15.79,
         "variation": -0.1, "percentChange": -0.61},
    ]}
    v = parse_india_vix(payload)
    assert v["last"] == 15.79
    assert v["change"] == -0.1
    assert v["pct_change"] == -0.61


def test_parse_india_vix_absent_or_empty():
    assert parse_india_vix({"data": [{"index": "NIFTY 50", "last": 1}]}) is None
    assert parse_india_vix({}) is None
    # present but no value -> None (don't fabricate a number)
    assert parse_india_vix({"data": [{"index": "INDIA VIX", "last": None}]}) is None
