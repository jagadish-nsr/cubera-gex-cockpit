"""WS vs REST spot-source probe (Pillar D4)."""

from __future__ import annotations

import time

from app.spot_ticker import _STORE, push_ws_tick, spot_source_status


def setup_function() -> None:
    _STORE.clear()


def test_ws_tick_marks_source_ws():
    push_ws_tick("NIFTY", ohlc={"ltp": 24500.0})
    st = spot_source_status(("NIFTY",))
    assert st["primary"] == "ws"
    assert st["per_symbol"]["NIFTY"]["source"] == "ws"


def test_rest_record_marks_primary_rest():
    _STORE["NIFTY"] = {
        "ohlc": {"ltp": 24500.0},
        "fut": None,
        "quote": {"spot": 24500.0},
        "at": time.time(),
        "ws_at": None,
        "source": "rest",
        "err": None,
    }
    st = spot_source_status(("NIFTY", "BANKNIFTY"))
    assert st["primary"] == "rest"
    assert st["per_symbol"]["NIFTY"]["source"] == "rest"
    assert st["per_symbol"]["BANKNIFTY"]["source"] == "none"
