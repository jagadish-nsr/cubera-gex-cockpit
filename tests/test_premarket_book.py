"""Which book the premarket card reads, and which open it trusts (25 Nov findings).

25 Nov: Nifty and Bank Nifty weekly series expired on Mon 23 Nov (24 Nov was a
holiday). The stored close row was an empty chain: net GEX exactly 0, flip equal to
spot, walls at the nearest strike. The card quoted that as a "shared gamma strike"
while the chain published at 09:07 held the real book.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from data import pre_post_market as ppm
from data.market_snapshot import book_degenerate_reason
from data.pre_post_market import classify_opening_gap, get_pre_market_state

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 25, 9, 9, 30, tzinfo=IST)

# The Nov 23 post-expiry close row as it was read on 25 Nov.
EXPIRED_CLOSE = {
    "spot": 25349.65,
    "prev_close": 25514.3,
    "call_wall": 25350.0,
    "put_wall": 25350.0,
    "gamma_flip": 25349.65,
    "net_gex": 0.0,
    "as_of": "2026-11-23T10:01:00+00:00",
}
LIVE_BOOK = {
    "call_wall": 26000.0,
    "put_wall": 25500.0,
    "gamma_flip": 25225.67,
    "net_gex": 4.1e10,
    "spot": 25349.65,
    "as_of": "2026-11-25T09:07:10+05:30",
}


def test_book_degenerate_reason_hand_checked():
    # Exact zero is an empty chain.
    assert "zero" in book_degenerate_reason(25350.0, 25350.0, 25349.65, 0.0, 25349.65)
    # Flip on spot with one shared strike is the same symptom when net GEX is missing.
    assert book_degenerate_reason(62500.0, 62500.0, 62585.0, None, 62585.35)
    # A real book: separate walls, flip away from spot, non-zero net.
    assert book_degenerate_reason(26000.0, 25500.0, 25225.67, 4.1e10, 25349.65) == ""
    # Shared strike alone is not degenerate (real books share a strike sometimes).
    assert book_degenerate_reason(25500.0, 25500.0, 25225.0, 3.0e9, 25349.65) == ""
    # Unknown net GEX is not flagged by itself.
    assert book_degenerate_reason(26000.0, 25500.0, 25225.67, None, 25349.65) == ""


def test_classify_without_any_book_reads_only_the_gap():
    res = classify_opening_gap(308.7, 1.22, 25658.35, 0.0, 0.0, 0.0, None, strike_step=50)
    assert res["regime"] == "BOOK_UNAVAILABLE"
    assert "MAP ONLY" in res["favored_play"]
    assert "NO BOOK" in res["favored_play"]
    for word in ("squeeze", "cascade", "shared"):
        assert word not in res["briefing"].lower().replace("no wall, balance or book sign", "")
    # A flip alone is a book of sorts: the normal classifier still runs.
    assert classify_opening_gap(308.7, 1.22, 25658.35, 0.0, 0.0, 25300.0, 1.0, strike_step=50)["regime"] != "BOOK_UNAVAILABLE"


def _chain(call_wall=26000.0, put_wall=25500.0, flip=25225.67, net=4.1e10, **over):
    levels = SimpleNamespace(
        call_wall=call_wall, put_wall=put_wall, flip=flip, total_net_gex=net,
        spot=25349.65, oi_stale=False,
    )
    data = SimpleNamespace(levels=levels, is_degraded=False)
    data.__dict__.update(over)
    return {"data": data, "fetched": NOW - timedelta(minutes=2)}


def test_published_book_accepts_a_live_chain():
    with patch("app.live_fetcher.latest", return_value=_chain()):
        book = ppm._published_book("NIFTY", NOW)
    assert book is not None
    assert (book["call_wall"], book["put_wall"], book["gamma_flip"]) == (26000.0, 25500.0, 25225.67)
    assert book["net_gex"] == 4.1e10


@pytest.mark.parametrize(
    "pub",
    [
        _chain(net=0.0),                                   # expired series
        _chain(flip=0.0),                                  # flip never crossed
        _chain(put_wall=0.0),                              # a wall missing
        _chain(is_degraded=True),                          # degraded feed
        {"data": _chain()["data"], "fetched": NOW - timedelta(days=2)},  # another day
    ],
    ids=["zero-gamma", "no-flip", "no-wall", "degraded", "yesterday"],
)
def test_published_book_refuses_unusable_chains(pub):
    with patch("app.live_fetcher.latest", return_value=pub), patch("builtins.open", side_effect=OSError):
        assert ppm._published_book("NIFTY", NOW) is None


def test_published_book_refuses_stale_oi():
    pub = _chain()
    pub["data"].levels.oi_stale = True
    with patch("app.live_fetcher.latest", return_value=pub), patch("builtins.open", side_effect=OSError):
        assert ppm._published_book("NIFTY", NOW) is None


def _discover(live_book, stored=EXPIRED_CLOSE):
    ppm._PRE_MARKET_CACHE.clear()
    ohlc = {
        "open": 25658.35, "ltp": 25658.35, "prev_close": 25349.65,
        "as_of": "2026-11-25 09:08:47", "high": 25700.0, "low": 25600.0,
    }
    with patch("data.market_snapshot.get_snapshot", return_value=dict(stored)), \
         patch("data.pre_post_market._published_book", return_value=live_book), \
         patch("data.dhan_live.fetch_index_ohlc", return_value=ohlc), \
         patch("data.pre_post_market.save_pre_market_state"), \
         patch("data.pre_post_market.persist_opening_watch_from_state"), \
         patch("data.pre_post_market.attach_pre_market_shocks", side_effect=lambda s: s), \
         patch("data.pre_post_market._tape_sample"), \
         patch("data.pre_post_market.get_persisted_auction_record", return_value=None), \
         patch("data.pre_post_market.ensure_pre_market_poller"):
        try:
            return get_pre_market_state("NIFTY", force_refresh=True, now=NOW)
        finally:
            ppm._PRE_MARKET_CACHE.clear()


def test_discovery_reads_the_live_chain_not_the_expired_close():
    state = _discover(dict(LIVE_BOOK))
    assert state["is_discovered"] is True
    assert state["book_source"] == "live-chain"
    assert (state["call_wall"], state["put_wall"]) == (26000.0, 25500.0)
    # Spot 25,658 is above the balance (25,226) and 342 pts under the call wall.
    assert state["gap_regime"] == "GAP_UP_INTO_POS_GAMMA"
    assert "shared" not in state["regime_briefing"].lower()
    assert state["net_gex"] == 4.1e10
    # The chain was priced at the prior close (25,350); the open is 25,658.
    assert "computed at spot 25,350" in state["book_note"]


def test_book_spot_note_only_when_the_open_has_left_the_book_spot():
    assert ppm._book_spot_note(25349.65, 25658.35)
    assert ppm._book_spot_note(25649.0, 25658.35) == ""  # within 0.25%
    assert ppm._book_spot_note(0.0, 25658.35) == ""
    assert ppm._book_spot_note(25349.65, 0.0) == ""


def test_discovery_without_live_chain_refuses_the_expired_close():
    state = _discover(None)
    assert state["is_discovered"] is True
    assert state["book_source"] == "unavailable"
    assert "zero" in state["book_note"]
    assert (state["call_wall"], state["put_wall"], state["gamma_flip"]) == (0.0, 0.0, 0.0)
    assert state["gap_regime"] == "BOOK_UNAVAILABLE"
    assert "MAP ONLY" in state["favored_play"]
    assert "shared" not in state["regime_briefing"].lower()


def test_discovery_keeps_a_usable_prior_close_when_no_live_chain():
    stored = dict(EXPIRED_CLOSE, call_wall=26000.0, put_wall=25500.0, gamma_flip=25225.67, net_gex=4.1e10)
    state = _discover(None, stored=stored)
    assert state["book_source"] == "prior-session"
    assert (state["call_wall"], state["put_wall"]) == (26000.0, 25500.0)
    assert state["gap_regime"] == "GAP_UP_INTO_POS_GAMMA"


def test_store_open_far_from_the_live_print_is_not_an_open():
    """23 and 25 Nov: the WS store open was 25,368.00 while the auction printed ~25,650."""
    now = datetime(2026, 11, 25, 9, 9, tzinfo=IST)
    with patch("data.pre_post_market._store_live_print",
               return_value=(25368.0, 25651.0, 25349.65, "2026-11-25 09:08:30")), \
         patch("app.spot_ticker._ws_is_live_for", return_value=True):
        px, _prev, src, _as_of = ppm._resolve_discovery_print("NIFTY", now, None, fallback_prev=25349.65)
    assert src == "ws-ltp"
    assert px == 25651.0


def test_store_open_near_the_live_print_is_still_used():
    now = datetime(2026, 11, 25, 9, 9, tzinfo=IST)
    with patch("data.pre_post_market._store_live_print",
               return_value=(25655.0, 25651.0, 25349.65, "2026-11-25 09:08:30")):
        px, _prev, src, _as_of = ppm._resolve_discovery_print("NIFTY", now, None, fallback_prev=25349.65)
    assert (src, px) == ("spot-open", 25655.0)


def test_a_spot_open_lock_is_provisional_until_the_dhan_day_open():
    assert "spot-open" in ppm._LTP_OPEN_SOURCES
