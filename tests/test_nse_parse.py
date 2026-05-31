"""Parse the official NSE option-chain JSON shape (offline, no network).

Pins the mapping quirks: IV is PERCENT -> decimal, CE/PE -> C/P, expiry date
format "%d-%b-%Y", spot from records.underlyingValue, bid/ask mid preferred.
"""

from datetime import date

from data.nse import parse_nse_option_chain


def _payload():
    return {
        "records": {
            "underlyingValue": 24500.5,
            "expiryDates": ["25-Jun-2026"],
            "data": [
                {
                    "strikePrice": 24500,
                    "expiryDate": "25-Jun-2026",
                    "CE": {"openInterest": 1200, "impliedVolatility": 13.45,
                           "lastPrice": 150.0, "bidprice": 149.0, "askPrice": 151.0},
                    "PE": {"openInterest": 1800, "impliedVolatility": 14.10,
                           "lastPrice": 120.0, "bidprice": 0, "askPrice": 0},
                },
                {
                    "strikePrice": 24600,
                    "expiryDate": "25-Jun-2026",
                    "CE": {"openInterest": 800, "impliedVolatility": 0,
                           "lastPrice": 0, "bidprice": 0, "askPrice": 0},
                    # PE intentionally missing -> should be skipped
                },
            ],
        }
    }


def test_parse_shapes_and_scaling():
    rows, spot = parse_nse_option_chain(_payload())
    assert spot == 24500.5

    by = {(r["strike"], r["type"]): r for r in rows}
    ce = by[(24500.0, "C")]
    assert ce["expiry"] == date(2026, 6, 25)
    assert ce["oi"] == 1200
    assert abs(ce["iv"] - 0.1345) < 1e-9          # percent -> decimal
    assert ce["price"] == 150.0                   # bid/ask mid (149+151)/2

    pe = by[(24500.0, "P")]
    assert pe["price"] == 120.0                   # no bid/ask -> lastPrice
    assert abs(pe["iv"] - 0.1410) < 1e-9

    # 24600 CE has zero IV/price -> None (engine derives/flags), PE absent -> skipped
    ce2 = by[(24600.0, "C")]
    assert ce2["iv"] is None and ce2["price"] is None
    assert (24600.0, "P") not in by
