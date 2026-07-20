"""Targeted option quotes — OI + premium for the 2-3 WATCHED strikes, without pulling the chain.

The gamma-blast score needs writer ΔOI and premium expansion, which used to force a full-chain
pull (one throttled /optionchain call per expiry, ~12s) just to read two rows. Dhan's marketfeed
quote returns `oi` and `last_price` for specific option contracts, so the same data comes back in
one ~0.5s call — verified live 20-Jul-2026.
"""

import data.dhan_live as dl


def _fake_master(monkeypatch):
    idx = {"day": "9999-01-01", "seg": "NSE_FNO", "expiry": "2026-07-21",
           "by_strike": {24200.0: {"CE": 57348, "PE": 57349},
                         24250.0: {"CE": 57350, "PE": 57351}}}
    monkeypatch.setattr(dl, "_option_contracts", lambda *a, **k: idx)
    return idx


def _quote(monkeypatch, cells, ok=True):
    class _R:
        def __init__(self):
            self.ok = ok

        @staticmethod
        def json():
            return {"data": {"NSE_FNO": cells}}

    sent = {}

    def _post(path, body, timeout=15):
        sent["path"], sent["body"] = path, body
        return _R()

    monkeypatch.setattr(dl, "_post", _post)
    return sent


def test_only_the_watched_contracts_are_requested(monkeypatch):
    _fake_master(monkeypatch)
    sent = _quote(monkeypatch, {
        "57348": {"oi": 7129460, "last_price": 104.2, "last_trade_time": "20/07/2026 15:29:59"},
        "57349": {"oi": 13575380, "last_price": 45.65, "last_trade_time": "20/07/2026 15:29:59"}})
    q = dl.fetch_option_quotes("NIFTY", [24200])
    assert sent["body"] == {"NSE_FNO": [57348, 57349]}      # 2 contracts, not a chain
    assert q[24200.0] == {"call_oi": 7129460.0, "call_ltp": 104.2,
                          "put_oi": 13575380.0, "put_ltp": 45.65}
    assert q["as_of"] == "2026-07-20 15:29:59"
    assert q["expiry"] == "2026-07-21"


def test_multiple_strikes_ride_one_call(monkeypatch):
    _fake_master(monkeypatch)
    sent = _quote(monkeypatch, {
        "57348": {"oi": 1, "last_price": 1.0}, "57349": {"oi": 2, "last_price": 2.0},
        "57350": {"oi": 3, "last_price": 3.0}, "57351": {"oi": 4, "last_price": 4.0}})
    q = dl.fetch_option_quotes("NIFTY", [24200, 24250])
    assert sent["body"] == {"NSE_FNO": [57348, 57349, 57350, 57351]}   # ONE request
    assert q[24250.0]["call_oi"] == 3.0 and q[24250.0]["put_ltp"] == 4.0


def test_unknown_strike_is_skipped_not_guessed(monkeypatch):
    _fake_master(monkeypatch)
    sent = _quote(monkeypatch, {"57348": {"oi": 1, "last_price": 1.0},
                                "57349": {"oi": 2, "last_price": 2.0}})
    dl.fetch_option_quotes("NIFTY", [24200, 99999])
    assert sent["body"] == {"NSE_FNO": [57348, 57349]}


def test_failures_are_soft(monkeypatch):
    _fake_master(monkeypatch)
    _quote(monkeypatch, {}, ok=False)
    assert dl.fetch_option_quotes("NIFTY", [24200]) is None
    monkeypatch.setattr(dl, "_option_contracts", lambda *a, **k: {"seg": None, "by_strike": {}})
    assert dl.fetch_option_quotes("NIFTY", [24200]) is None


def test_ticker_read_is_instant_and_drops_stale(monkeypatch):
    import time

    from app import blast_ticker as bt
    bt._STORE.clear()
    t = time.time()
    assert bt.latest("NIFTY") is None
    assert (time.time() - t) < 0.05                       # never blocks the render
    bt._STORE["NIFTY"] = {"q": {24200.0: {"call_oi": 1}}, "at": time.time() - 999}
    assert bt.latest("NIFTY") is None                     # too old to present as live
    bt._STORE["NIFTY"] = {"q": {24200.0: {"call_oi": 1}}, "at": time.time()}
    assert bt.latest("NIFTY")[24200.0]["call_oi"] == 1
    bt._STORE.clear()


def test_share_denominated_oi_is_fine_for_the_score():
    """The score uses ΔOI/OI ratios, so Dhan's share-denominated OI needs no lot conversion —
    unlike the ₹ turnover, which did (65x bug)."""
    from app.gammablast import blast_score
    lots = [{"call_oi": 100.0, "put_oi": 50.0, "call_ltp": 10.0, "put_ltp": 5.0},
            {"call_oi": 80.0, "put_oi": 50.0, "call_ltp": 14.0, "put_ltp": 5.0}]
    shares = [{k: (v * 65 if k.endswith("_oi") else v) for k, v in row.items()} for row in lots]
    assert blast_score("up", lots)["score"] == blast_score("up", shares)["score"]
