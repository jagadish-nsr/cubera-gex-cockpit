"""NSE adapter mapping + end-to-end compute_levels on a parity-consistent
synthetic Nifty chain (no live network)."""

import math
from datetime import date, datetime, timezone

from data.nse import from_nse_results, years_to_expiry
from gex.bs import bs_call_price
from gex.levels import compute_levels

FETCHED = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)  # ~11:30 IST


def _parity_consistent_nifty(spot=24500.0, r=0.066, q=0.013, iv=0.12):
    """Rows whose call/put prices satisfy parity for planted (r, q), so the
    engine recovers them and runs clean. Uses the adapter's own T."""
    expiry = "2026-06-25"  # a Thursday-ish monthly placeholder
    T = years_to_expiry(date(2026, 6, 25), FETCHED)
    DF = math.exp(-r * T)
    F = spot * math.exp((r - q) * T)
    strikes = [24300, 24400, 24500, 24600, 24700, 24800]
    rows = []
    for k in strikes:
        call = float(bs_call_price(spot, k, T, iv, r, q))
        put = call - DF * (F - k)  # parity -> consistent & positive
        rows.append({"expiry": expiry, "strike": k, "type": "CE", "oi": 1500, "iv": iv,
                     "price": max(call, 0.5)})
        rows.append({"expiry": expiry, "strike": k, "type": "PE", "oi": 1800, "iv": iv,
                     "price": max(put, 0.5)})
    return rows, spot, r, q


def test_mapping_and_types():
    rows, spot, *_ = _parity_consistent_nifty()
    snap = from_nse_results(
        rows, spot=spot, expiry_window="0dte_to_next_monthly",
        dealer_sign_model="long_call_short_put", fetched_at=FETCHED, oi_timestamp=FETCHED,
    )
    snap.validate()
    assert snap.spot == spot
    assert set(snap.contracts["type"]) == {"C", "P"}   # CE/PE normalised
    assert (snap.contracts["T"] > 0).all()


def test_end_to_end_levels():
    rows, spot, r, q = _parity_consistent_nifty()
    snap = from_nse_results(
        rows, spot=spot, expiry_window="0dte_to_next_monthly",
        dealer_sign_model="long_call_short_put", fetched_at=FETCHED, oi_timestamp=FETCHED,
    )
    levels = compute_levels(snap, contract_size=75, oi_stale_hours=72.0)
    (fit,) = list(levels.parity_fits.values())
    assert not fit.flagged
    assert fit.r == _close(r)
    assert fit.q == _close(q)
    assert levels.call_wall in {24300, 24400, 24500, 24600, 24700, 24800}
    assert levels.regime in {"POSITIVE", "NEGATIVE", "FLAT"}


def _close(x, tol=1e-3):
    class _A:
        def __eq__(self, other):
            return abs(other - x) < tol
    return _A()
