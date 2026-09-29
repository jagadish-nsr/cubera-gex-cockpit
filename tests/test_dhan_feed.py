"""Decoder pinned against REAL captured Dhan feed packets (NIFTY future, 2026-07-22 live).
These hex fixtures + their decoded values lock the byte offsets — if the layout assumption ever
drifts, these fail. Captured & verified via the live feed (scripts/dhan_ws_probe.py lineage)."""

from data.dhan_feed import decode_packet
from app.big_money import classify_aggressor

# One live Quote (code 4, 50B) and Full (code 8, 162B) packet for the NIFTY Jul-2026 future (id 61093).
QUOTE_HEX = ("04320002a5ee0000cd84bb46410080c7606a29a2bb46ef9b2400cbd90500389802009a7cbc4633e9bc460092"
             "bc460012bb46")
FULL_HEX = ("08a20002a5ee0000cd84bb46410080c7606a29a2bb46ef9b2400cbd9050038980200c98add00c01ade00deb4"
            "da009a7cbc4633e9bc460092bc460012bb468a0200008a020000010004000078bb460086bb46410000008e03"
            "0000010004003376bb460088bb46410000008a020000010002000076bb46cd89bb4682000000cf0300000200"
            "0500006cbb46008abb46410000004501000001000100666bbb46338abb46")


def test_decode_quote():
    t = decode_packet(bytes.fromhex(QUOTE_HEX))
    assert t.code == 4 and t.name == "Quote"
    assert t.security_id == 61093
    assert abs(t.ltp - 24002.40) < 0.01
    assert t.ltq == 65                        # exactly one NIFTY lot
    assert t.volume == 2399215
    assert t.tot_sell_qty == 383435 and t.tot_buy_qty == 170040
    assert 23000 < t.low < t.high < 25000 and t.close > 0     # OCHL sane, prev-close present
    assert t.bid is None and t.ask is None    # Quote tier has no depth


def test_decode_full():
    t = decode_packet(bytes.fromhex(FULL_HEX))
    assert t.code == 8 and t.name == "Full"
    assert t.security_id == 61093
    assert abs(t.ltp - 24002.40) < 0.01
    assert t.ltq == 65
    assert t.volume == 2399215
    assert t.oi == 14518985                    # ~223k lots — sane NIFTY-future OI
    # best bid/ask from depth level 1, bracketing/near the LTP
    assert abs(t.bid - 23996.0) < 0.01 and abs(t.ask - 24003.0) < 0.01
    assert t.bid < t.ask


def test_decode_full_aggressor_indeterminate_here():
    """This real print sat INSIDE the spread (24002.4 in 23996/24003) → Lee-Ready returns None."""
    t = decode_packet(bytes.fromhex(FULL_HEX))
    assert t.bid < t.ltp < t.ask
    assert classify_aggressor(t.ltp, t.bid, t.ask) is None


def test_decode_short_frame_is_none():
    assert decode_packet(b"") is None
    assert decode_packet(b"\x08\x00\x00") is None
