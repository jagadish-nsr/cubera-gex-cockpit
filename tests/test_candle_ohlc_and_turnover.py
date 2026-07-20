"""Two real glitches, both of which showed WRONG NUMBERS rather than failing loudly.

1. The candle printed green up to ~24,300 while spot was 24,182 — it used the feed's `close`
   (the PREVIOUS session's, around the open) as a developing candle's close, and the stale high
   survived the union with spot.
2. ₹ turnover in "where the money is building" was overstated by a whole lot size on the Dhan
   feed, because Dhan counts OI in SHARES while the formula multiplied by lot_size anyway.
"""

import pandas as pd
import pytest

from app.oi_footprint import window_footprint
from app.simple_dashboard import _oi_unit, _resolve_ohlc


from datetime import datetime, timezone


class _D:
    # fetched_at 2026-07-20 07:48 UTC = 13:18 IST (the live tape the semantics were verified on)
    def __init__(self, day_ohlc, source="dhan"):
        self.day_ohlc = day_ohlc
        self.source = source
        self.fetched_at = datetime(2026, 7, 20, 7, 48, tzinfo=timezone.utc)


HL = {"hi": 24210.0, "lo": 24150.0, "open": 24200.0}


def test_stale_previous_session_ohlc_is_rejected():
    """The reported glitch: yesterday's block (close/high ~24,334) beside a live spot of 24,182."""
    stale = {"open": 24127.6, "high": 24334.3, "low": 24100.0, "prev_close": 24334.3,
             "ltp": 24334.3}
    oh = _resolve_ohlc(_D(stale), HL, spot=24182.0)
    assert oh["c"] == 24182.0                      # close is the live price, never the feed's close
    assert oh["h"] <= 24210.0                      # the stale high is gone, not merely unioned
    assert oh["o"] == 24200.0                      # fell back to our own captured open
    assert oh["true_ohlc"] is False                # and it says so


def test_fresh_ohlc_is_still_used():
    fresh = {"open": 24160.0, "high": 24245.0, "low": 24140.0, "prev_close": 24334.3,
             "ltp": 24182.5}
    oh = _resolve_ohlc(_D(fresh), HL, spot=24182.0)
    assert oh["true_ohlc"] is True
    assert oh["h"] == 24245.0 and oh["l"] == 24140.0 and oh["o"] == 24160.0
    assert oh["c"] == 24182.0


def test_ohlc_without_an_ltp_cannot_be_verified_so_is_not_trusted():
    oh = _resolve_ohlc(_D({"open": 1.0, "high": 99999.0, "low": 1.0, "prev_close": 9.0}), HL, 24182.0)
    assert oh["h"] <= 24210.0 and oh["true_ohlc"] is False


def test_candle_never_closes_away_from_spot():
    """The invariant the glitch violated, over a spread of feed states."""
    for d in ({}, {"prev_close": 24334.0, "ltp": 24334.0}, {"prev_close": 24000.0, "ltp": 24182.0},
              {"open": 24160.0, "high": 24245.0, "low": 24140.0, "prev_close": 24334.3,
               "ltp": 24182.0, "as_of": "2026-07-20 13:18:23"}):
        assert _resolve_ohlc(_D(d), HL, spot=24182.0)["c"] == 24182.0


# ── turnover units ─────────────────────────────────────────────────────────────────────────────
LOT = 65


def _df():
    """Two snapshots; one strike adds 10 lots of calls at a ₹100 premium → ₹65,000 of turnover."""
    rows = []
    for t, coi in (("10:00", 100 * LOT), ("10:30", 110 * LOT)):     # OI in SHARES (Dhan style)
        rows.append({"time_ist": t, "strike": 24200.0, "call_oi_near": coi, "put_oi_near": 0.0,
                     "call_ltp": 100.0, "put_ltp": 50.0})
    return pd.DataFrame(rows)


def test_dhan_share_denominated_oi_is_not_multiplied_by_the_lot_again():
    w = window_footprint(_df(), 15, spot=24200.0, band_pts=500, now_hm=630,
                         lot_size=LOT, oi_unit="shares")
    assert w["unit"] == "inr"
    assert w["flow_val"] == pytest.approx(10 * LOT * 100.0)        # 65,000 — not 65,000 × 65


def test_nse_lot_denominated_oi_still_gets_the_lot_multiplier():
    df = _df()
    df[["call_oi_near"]] = df[["call_oi_near"]] / LOT               # same book counted in LOTS
    w = window_footprint(df, 15, spot=24200.0, band_pts=500, now_hm=630,
                         lot_size=LOT, oi_unit="lots")
    assert w["flow_val"] == pytest.approx(10 * LOT * 100.0)        # identical ₹ from either feed


def test_oi_unit_is_read_from_the_feed():
    assert _oi_unit("dhan") == "shares"
    assert _oi_unit("dhan (replay)") == "shares"
    assert _oi_unit("nse") == "lots"
    assert _oi_unit("nse (Dhan fallback)") == "lots"
    assert _oi_unit(None) == "lots"


# ── the feed's own timestamp is the authoritative freshness check (user's suggestion) ──────────
def test_todays_timestamp_makes_the_block_trusted():
    d = {"open": 24190.05, "high": 24262.35, "low": 24135.85, "prev_close": 24334.3,
         "ltp": 24218.0, "as_of": "2026-07-20 13:18:23"}
    oh = _resolve_ohlc(_D(d), HL, spot=24218.0)
    assert oh["true_ohlc"] is True
    assert oh["h"] == 24262.35 and oh["l"] == 24135.85


def test_yesterdays_timestamp_rejects_the_block_even_if_the_prices_look_plausible():
    """The case an ltp-vs-spot check CANNOT catch: a whole previous-session block whose ltp happens
    to sit near today's spot. Only the timestamp settles it."""
    d = {"open": 24190.0, "high": 24999.0, "low": 24100.0, "prev_close": 24000.0,
         "ltp": 24218.0, "as_of": "2026-07-17 15:29:59"}
    oh = _resolve_ohlc(_D(d), HL, spot=24218.0)
    assert oh["true_ohlc"] is False
    assert oh["h"] <= 24218.0          # yesterday's 24,999 high is gone


def test_trade_time_parsing():
    from data.dhan_live import _trade_time
    assert _trade_time({"last_trade_time": "20/07/2026 13:18:23"}).day == 20
    assert _trade_time({"last_trade_time": ""}) is None
    assert _trade_time({}) is None
    assert _trade_time({"last_trade_time": "garbage"}) is None
