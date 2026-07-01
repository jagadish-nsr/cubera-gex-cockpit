"""Bundled gamma-blast trigger: the expiry/window predicates and the 1-min
blast write. These are the guards that decide when the 15-min time-series task
switches to the 1-min gamma-blast capture (no separate scheduled task)."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import scripts.log_timeseries as lt


def _ist(h, m):
    # _in_blast_window reads .hour/.minute/.weekday() directly (already IST-local,
    # as produced by _ist_now); 2026-06-30 is a Tuesday.
    return datetime(2026, 6, 30, h, m)


def test_in_blast_window_bounds():
    # blast window 13:00-15:30 IST (Tue 2026-06-30 is a weekday).
    assert not lt._in_blast_window(_ist(12, 59))  # 12:59 IST — before
    assert lt._in_blast_window(_ist(13, 0))       # 13:00 IST — opens
    assert lt._in_blast_window(_ist(14, 15))      # 14:15 IST — inside
    assert lt._in_blast_window(_ist(15, 30))      # 15:30 IST — closes (inclusive)
    assert not lt._in_blast_window(_ist(15, 31))  # 15:31 IST — shut


def test_is_expiry_day_matches_nearest_expiry_today():
    # fetched 05:00 UTC 2026-06-30 -> 10:30 IST 2026-06-30.
    fa = datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc)
    today = SimpleNamespace(fetched_at=fa, expiries=[date(2026, 6, 30), date(2026, 7, 7)])
    later = SimpleNamespace(fetched_at=fa, expiries=[date(2026, 7, 7)])
    empty = SimpleNamespace(fetched_at=fa, expiries=[])
    assert lt._is_expiry_day(today) is True     # nearest expiry == today
    assert lt._is_expiry_day(later) is False    # nearest is a week out
    assert lt._is_expiry_day(empty) is False    # no expiries -> never


def test_save_blast_writes_to_gamma_blast_dir(tmp_path, monkeypatch):
    import app.timeseries as ts
    monkeypatch.setattr(lt, "GAMMA_BLAST_DIR", tmp_path)
    data = SimpleNamespace(
        symbol="BANKNIFTY",
        fetched_at=datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc),
        levels=SimpleNamespace(spot=57000.0),
        oi_strikes=[{"strike": 57000.0, "call_oi": 1, "call_doi": 2, "put_oi": 3,
                     "put_doi": 4, "call_vol": 5, "put_vol": 6,
                     "call_ltp": 100.0, "put_ltp": 110.0, "net_gex": 9}],
    )
    n = lt._save_blast(data)
    assert n == 1
    # landed in the per-symbol gamma-blast subfolder, not the plain oi_strikes tree
    df = ts.load_strikes_day("2026-06-30", symbol="BANKNIFTY", directory=tmp_path)
    assert len(df) == 1 and df["strike"].iloc[0] == 57000.0
    # demo-style bundle with no per-strike data -> no-op
    assert lt._save_blast(SimpleNamespace(symbol="NIFTY",
                          fetched_at=data.fetched_at,
                          levels=SimpleNamespace(spot=1.0), oi_strikes=[])) == 0
