"""ws_spot_feed packet decode — Full must surface OI (Quote does not)."""
from app.ws_spot_feed import _decode_packet
from tests.test_dhan_feed import FULL_HEX, QUOTE_HEX


def test_quote_has_ltp_but_no_oi():
    pkt = _decode_packet(bytes.fromhex(QUOTE_HEX))
    assert pkt is not None
    assert pkt["code"] == 4
    assert pkt["security_id"] == 61093
    assert abs(pkt["ltp"] - 24002.40) < 0.01
    assert "oi" not in pkt


def test_full_carries_oi_for_sentinel():
    pkt = _decode_packet(bytes.fromhex(FULL_HEX))
    assert pkt is not None
    assert pkt["code"] == 8
    assert pkt["security_id"] == 61093
    assert abs(pkt["ltp"] - 24002.40) < 0.01
    assert pkt["oi"] == 14518985
    assert abs(pkt["bid"] - 23996.0) < 0.01
    assert abs(pkt["ask"] - 24003.0) < 0.01
