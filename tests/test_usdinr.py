"""USD/INR parsing from the NSE marketStatus payload (hand-checkable)."""

from data.nse import parse_usdinr


def test_parse_usdinr_found_live():
    payload = {"marketState": [
        {"market": "Capital Market", "last": 23622.9, "variation": 461.3, "percentChange": 1.99},
        {"market": "currencyfuture", "last": "95.2200", "variation": "0.15",
         "percentChange": "0.16", "tradeDate": "12-Jun-2026"},
    ]}
    u = parse_usdinr(payload)
    assert u["last"] == 95.22
    assert u["change"] == 0.15
    assert u["pct_change"] == 0.16
    assert u["as_of"] == "12-Jun-2026"


def test_parse_usdinr_blank_at_close():
    # At close NSE returns the entry with an empty last -> None (don't fabricate).
    payload = {"marketState": [
        {"market": "currencyfuture", "last": "", "variation": "", "percentChange": ""},
    ]}
    assert parse_usdinr(payload) is None


def test_parse_usdinr_absent():
    assert parse_usdinr({"marketState": [{"market": "Capital Market", "last": 1}]}) is None
    assert parse_usdinr({}) is None
