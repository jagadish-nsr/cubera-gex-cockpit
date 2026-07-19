"""Sector snap — % change vs the previous day's close.

The load-bearing rule: NSE states ``percentChange`` outright, so it is primary. Dhan gives no
explicit prev-close, and its ``ohlc.close`` collapses onto the last price after hours — so the
Dhan path must report None (rendered '—') rather than print a wrong or 0.00% move. CLAUDE.md:
fail loud, never paper over.
"""

import data.sectors as sec


def _reset():
    sec._cache.update(at=0.0, rows=None, src=None)


def test_nse_rows_use_the_exchanges_own_percent_change(monkeypatch):
    payload = {"data": [
        {"index": "NIFTY BANK", "percentChange": 1.63, "last": 58521.4},
        {"index": "NIFTY IT", "percentChange": -0.42, "last": 29226.6},
        {"index": "NIFTY 50", "percentChange": 1.09, "last": 24334.3},
    ]}

    class _R:
        status_code = 200

        @staticmethod
        def json():
            return payload

    class _S:
        @staticmethod
        def get(*_a, **_k):
            return _R()

    rows = sec.fetch_sectors_nse(session=_S())
    got = {r["label"]: r for r in rows}
    assert got["Bank"]["pct"] == 1.63
    assert got["IT"]["pct"] == -0.42
    assert got["Bank"]["group"] == "sector" and got["Nifty 50"]["group"] == "broad"
    # indices absent from the payload are skipped, never invented
    assert "Pharma" not in got


def _dhan_cells(monkeypatch, cell_for):
    class _R:
        ok = True

        @staticmethod
        def json():
            return {"data": {"IDX_I": {str(sid): cell_for(sid)
                                       for _n, _l, sid in sec.SECTORS + sec.BROAD}}}

    monkeypatch.setattr("data.dhan_live._post", lambda *a, **k: _R())
    monkeypatch.setattr("data.dhan_live._num", lambda x: None if x is None else float(x))


def test_dhan_refuses_to_guess_when_close_equals_last(monkeypatch):
    """Post-session Dhan returns ohlc.close == last_price (today's close, NOT a baseline).
    Deriving a % from that would print 0.00% for every sector — a silent lie."""
    _dhan_cells(monkeypatch, lambda sid: {"last_price": 100.0, "net_change": 0,
                                          "ohlc": {"close": 100.0, "open": 98.0}})
    rows = sec.fetch_sectors_dhan()
    assert rows and all(r["pct"] is None for r in rows)
    assert all(r["last"] == 100.0 for r in rows)      # the level is still shown


def test_dhan_derives_a_percent_when_close_is_a_real_baseline(monkeypatch):
    _dhan_cells(monkeypatch, lambda sid: {"last_price": 101.0, "net_change": 0,
                                          "ohlc": {"close": 100.0, "open": 99.0}})
    rows = sec.fetch_sectors_dhan()
    assert rows and all(abs(r["pct"] - 1.0) < 1e-9 for r in rows)


def test_dhan_prefers_net_change_when_present(monkeypatch):
    _dhan_cells(monkeypatch, lambda sid: {"last_price": 102.0, "net_change": 2.0,
                                          "ohlc": {"close": 50.0}})
    rows = sec.fetch_sectors_dhan()
    assert rows and all(abs(r["pct"] - 2.0) < 1e-9 for r in rows)   # 2 / (102-2) = 2%


def test_snap_sorts_leaders_first_and_parks_unknowns_last(monkeypatch):
    _reset()
    monkeypatch.setattr(sec, "fetch_sectors_nse", lambda *a, **k: [
        {"label": "A", "pct": -1.0, "last": 1, "group": "sector"},
        {"label": "B", "pct": None, "last": 1, "group": "sector"},
        {"label": "C", "pct": 2.0, "last": 1, "group": "sector"},
    ])
    snap = sec.fetch_sector_snap()
    assert [r["label"] for r in snap["rows"]] == ["C", "A", "B"]
    assert snap["source"] == "nse"
    _reset()


def test_snap_falls_back_to_dhan_and_never_raises(monkeypatch):
    _reset()
    monkeypatch.setattr(sec, "fetch_sectors_nse", lambda *a, **k: None)
    monkeypatch.setattr(sec, "fetch_sectors_dhan",
                        lambda *a, **k: [{"label": "A", "pct": 1.0, "last": 1, "group": "sector"}])
    assert sec.fetch_sector_snap()["source"] == "dhan"
    _reset()
    monkeypatch.setattr(sec, "fetch_sectors_dhan", lambda *a, **k: None)
    assert sec.fetch_sector_snap() is None          # both dead → panel just doesn't render
    _reset()
