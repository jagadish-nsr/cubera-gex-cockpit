"""Sector snap — % change vs the previous day's close.

The load-bearing rule: NSE states ``percentChange`` outright, so it is primary. Dhan gives no
explicit prev-close, and its ``ohlc.close`` collapses onto the last price after hours — so the
Dhan path must report None (rendered '—') rather than print a wrong or 0.00% move. CLAUDE.md:
fail loud, never paper over.
"""

import data.sectors as sec


def _reset():
    sec._cache.update(at=0.0, rows=None, src=None)
    sec._refreshing = False


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
    assert got["Bank"]["weight"] == 30.0 and got["Nifty 50"]["weight"] is None
    # indices absent from the payload are skipped, never invented
    assert "Pharma" not in got


def _dhan_cells(monkeypatch, cell_for):
    class _R:
        ok = True

        @staticmethod
        def json():
            return {"data": {"IDX_I": {str(sid): cell_for(sid)
                                       for _n, _l, sid, _w in sec.SECTORS + sec.BROAD}}}

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
    snap = sec.fetch_sector_snap(blocking=True)
    assert [r["label"] for r in snap["rows"]] == ["C", "A", "B"]
    assert snap["source"] == "nse"
    _reset()


def test_snap_falls_back_to_dhan_and_never_raises(monkeypatch):
    _reset()
    monkeypatch.setattr(sec, "fetch_sectors_nse", lambda *a, **k: None)
    monkeypatch.setattr(sec, "fetch_sectors_dhan",
                        lambda *a, **k: [{"label": "A", "pct": 1.0, "last": 1, "group": "sector"}])
    assert sec.fetch_sector_snap(blocking=True)["source"] == "dhan"
    _reset()
    monkeypatch.setattr(sec, "fetch_sectors_dhan", lambda *a, **k: None)
    assert sec.fetch_sector_snap(blocking=True) is None   # both dead → panel just doesn't render
    _reset()


def test_only_index_heavy_sectors_are_shown():
    """The strip answers 'what is moving Nifty', so zero-weight sector indices stay out. They live
    in EXCLUDED (documented, reversible) rather than being deleted."""
    labels = {lbl for _n, lbl, _id, _w in sec.SECTORS}
    assert {"Realty", "PSU Bank", "Media", "Infra"}.isdisjoint(labels)
    assert {lbl for _n, lbl, _id, _w in sec.EXCLUDED} == {"Realty", "PSU Bank", "Media", "Infra"}
    assert all(w and w > 0 for _n, _l, _i, w in sec.SECTORS)      # every shown sector carries weight


def test_weights_are_plausible_and_ordered():
    """A wrong weight is a silent lie on a trading screen. Guard the shape: Bank is the heaviest,
    Pvt Bank sits inside Bank, Pharma inside Healthcare, Oil & Gas inside Energy."""
    w = {lbl: wt for _n, lbl, _id, wt in sec.SECTORS}
    assert w["Bank"] == max(w.values())
    assert w["Pvt Bank"] < w["Bank"]              # subset
    assert w["Pharma"] <= w["Healthcare"]         # subset
    assert w["Oil & Gas"] <= w["Energy"]          # subset
    assert all(0 < v <= 40 for v in w.values())


def test_snap_never_blocks_the_caller(monkeypatch):
    """The chain and futures pull are the load-bearing data — this context panel must never sit on
    the fetch path. A cold call returns immediately (None) and refreshes in the background."""
    _reset()
    import time
    calls = []

    def _slow(*_a, **_k):
        calls.append(1)
        time.sleep(0.4)
        return [{"label": "A", "pct": 1.0, "last": 1, "group": "sector", "weight": 30.0}]

    monkeypatch.setattr(sec, "fetch_sectors_nse", _slow)
    t0 = time.time()
    assert sec.fetch_sector_snap() is None            # nothing cached yet, and it did NOT wait
    assert (time.time() - t0) < 0.15
    time.sleep(0.8)                                    # background thread lands
    assert sec.fetch_sector_snap()["rows"][0]["label"] == "A"
    assert len(calls) == 1
    for _ in range(20):                                # inside TTL → zero further fetches
        sec.fetch_sector_snap()
    assert len(calls) == 1
    _reset()


def test_refresh_interval_is_slow_enough_to_not_compete():
    assert sec._TTL_S >= 900.0                         # 15 min+; options/futures get the bandwidth


def test_nse_fetch_does_not_pay_for_akamai_priming(monkeypatch):
    """Regression: the panel vanished pre-market because fetch_sectors_nse built a PRIMED session,
    whose /option-chain warm-up GET hangs outside market hours (>20s) and blew the timeout.
    /api/allIndices answers from a bare session, so priming must not be on the happy path."""
    primed = []
    monkeypatch.setattr("data.nse._primed_session",
                        lambda *a, **k: primed.append(1) or (_ for _ in ()).throw(AssertionError))

    class _R:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [{"index": "NIFTY BANK", "percentChange": 1.0, "last": 100.0}]}

    class _Sess:
        headers: dict = {}

        def get(self, *_a, **_k):
            return _R()

    monkeypatch.setattr("requests.Session", lambda: _Sess())
    rows = sec.fetch_sectors_nse()
    assert rows and rows[0]["label"] == "Bank"
    assert not primed, "priming must not be used when the bare request succeeds"


def test_failed_pull_retries_soon_not_after_a_full_ttl(monkeypatch):
    """One bad pull must not hide the strip for the whole 15-min success TTL."""
    _reset()
    monkeypatch.setattr(sec, "fetch_sectors_nse", lambda *a, **k: None)
    monkeypatch.setattr(sec, "fetch_sectors_dhan", lambda *a, **k: None)
    sec._pull()
    import time as _t
    age = _t.time() - sec._cache["at"]
    assert sec._TTL_S - age <= sec._FAIL_BACKOFF_S + 1     # next retry within the short backoff
    _reset()
