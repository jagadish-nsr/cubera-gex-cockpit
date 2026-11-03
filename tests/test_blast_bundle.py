"""Bundled gamma-blast trigger: the expiry/window predicates and the 1-min
blast write. These are the guards that decide when the 15-min time-series task
switches to the 1-min gamma-blast capture (no separate scheduled task)."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import config
import scripts.log_timeseries as lt

_IST = timezone(timedelta(hours=5, minutes=30))


def _ist(h, m):
    # _in_blast_window reads .hour/.minute/.weekday() directly (already IST-local,
    # as produced by _ist_now); 2026-06-30 is a Tuesday.
    return datetime(2026, 6, 30, h, m)


def test_in_session_ten_am_ist_is_open():
    """10:00 IST is inside the cash session.

    The old helper returned 10:00 with a UTC tzinfo. is_market_open then
    added another 5:30 and read 15:30, which is the cash close, so the
    15-minute journal skipped every fire from 10:00 onward.
    """
    ten_am = datetime(2026, 10, 27, 10, 0, tzinfo=_IST)
    assert lt._in_session(ten_am) is True
    labeled_utc = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
    assert config.is_market_open(labeled_utc, include_fno_extended=False) is False


def test_ist_now_carries_ist_not_utc(monkeypatch):
    fixed_utc = datetime(2026, 10, 27, 4, 30, tzinfo=timezone.utc)  # 10:00 IST

    class _Clock:
        @staticmethod
        def now(tz=None):
            return fixed_utc if tz is None else fixed_utc.astimezone(tz)

    monkeypatch.setattr(lt, "datetime", _Clock)
    got = lt._ist_now()
    assert got.utcoffset() == timedelta(hours=5, minutes=30)
    assert got.hour == 10
    assert lt._in_session(got) is True


def test_in_blast_window_bounds():
    # 13:00 through the F&O close. 15:39 is still open; 15:40 is the close.
    assert not lt._in_blast_window(_ist(12, 59))
    assert lt._in_blast_window(_ist(13, 0))
    assert lt._in_blast_window(_ist(14, 15))
    assert lt._in_blast_window(_ist(15, 30))
    assert lt._in_blast_window(_ist(15, 39))
    assert not lt._in_blast_window(_ist(15, 40))


def test_next_blast_minute_keeps_a_late_pull():
    early = datetime(2026, 6, 30, 14, 15, 10)
    assert lt.seconds_until_next_blast_minute(early) == 50
    # Second 58 waits two seconds and lands in 14:16. It does not skip to 14:17.
    almost = datetime(2026, 6, 30, 14, 15, 58)
    assert lt.seconds_until_next_blast_minute(almost) == 2
    assert lt.next_blast_due(almost) == datetime(2026, 6, 30, 14, 16, 0)
    on_the_mark = datetime(2026, 6, 30, 14, 16, 0)
    assert lt.next_blast_due(on_the_mark) == datetime(2026, 6, 30, 14, 17, 0)


def test_other_process_does_not_append_the_blast_file(tmp_path, monkeypatch):
    lock = tmp_path / "gamma_blast.lock"
    lock.write_text("424242")
    monkeypatch.setattr(lt, "_blast_lock_path", lambda: lock)
    monkeypatch.setattr(lt, "_pid_alive", lambda pid: pid == 424242)
    assert lt._blast_already_running() is True
    monkeypatch.setattr(lt, "_pid_alive", lambda pid: False)
    assert lt._blast_already_running() is False


def test_is_expiry_day_matches_nearest_expiry_today():
    # fetched 05:00 UTC 2026-06-30 -> 10:30 IST 2026-06-30.
    fa = datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc)
    today = SimpleNamespace(fetched_at=fa, expiries=[date(2026, 6, 30), date(2026, 7, 7)])
    later = SimpleNamespace(fetched_at=fa, expiries=[date(2026, 7, 7)])
    empty = SimpleNamespace(fetched_at=fa, expiries=[])
    assert lt._is_expiry_day(today) is True     # nearest expiry == today
    assert lt._is_expiry_day(later) is False    # nearest is a week out
    assert lt._is_expiry_day(empty) is False    # no expiries -> never


def _blast_row(spot, call, put, fetched):
    return SimpleNamespace(
        symbol="NIFTY", source="dhan", fetched_at=fetched,
        levels=SimpleNamespace(spot=spot),
        oi_strikes=[{"strike": 23700.0, "call_oi": 1, "put_oi": 1,
                     "call_ltp": call, "put_ltp": put, "net_gex": -1}],
    )


def test_stuck_chain_spot_is_flagged_when_premiums_move(tmp_path, monkeypatch):
    import app.timeseries as ts
    monkeypatch.setattr(ts, "GAMMA_BLAST_DHAN_DIR", tmp_path)
    fetched = datetime(2026, 11, 3, 9, 45, tzinfo=timezone.utc)  # 15:15 IST
    # 15:15 print. Parity is 23,700 + 49.2 − 9.4.
    first = lt._save_blast(_blast_row(23731.8, 49.2, 9.4, fetched))
    assert first == 1
    later = fetched + timedelta(minutes=5)
    # Same chain spot, premiums moved. Parity is 23,700 + 55.0 − 5.6 = 23,749.4.
    assert lt._save_blast(_blast_row(23731.8, 55.0, 5.6, later)) == 1
    df = ts.load_strikes_day("2026-11-03", symbol="NIFTY", directory=tmp_path)
    assert list(df["spot_stale"]) == [0, 1]
    assert float(df["parity_spot"].iloc[1]) == 23749.4
    # A spot that actually moved is not flagged.
    moved = later + timedelta(minutes=1)
    assert lt._save_blast(_blast_row(23790.0, 90.0, 0.1, moved)) == 1
    df = ts.load_strikes_day("2026-11-03", symbol="NIFTY", directory=tmp_path)
    assert int(df["spot_stale"].iloc[-1]) == 0


def test_save_blast_writes_to_gamma_blast_dir(tmp_path, monkeypatch):
    import app.timeseries as ts
    # _save_blast routes via gamma_blast_dir_for(source); NSE/None -> GAMMA_BLAST_DIR (patch it here).
    monkeypatch.setattr(ts, "GAMMA_BLAST_DIR", tmp_path)
    data = SimpleNamespace(
        symbol="BANKNIFTY", source="nse",
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
    assert lt._save_blast(SimpleNamespace(symbol="NIFTY", source="nse",
                          fetched_at=data.fetched_at,
                          levels=SimpleNamespace(spot=1.0), oi_strikes=[])) == 0


def test_save_blast_routes_dhan_to_separate_dir(tmp_path, monkeypatch):
    """Dhan blast captures land in gamma_blast_dhan, never mixed with the NSE tree."""
    import app.timeseries as ts
    nse_dir, dhan_dir = tmp_path / "nse", tmp_path / "dhan"
    monkeypatch.setattr(ts, "GAMMA_BLAST_DIR", nse_dir)
    monkeypatch.setattr(ts, "GAMMA_BLAST_DHAN_DIR", dhan_dir)
    data = SimpleNamespace(
        symbol="NIFTY", source="dhan",
        fetched_at=datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc),
        levels=SimpleNamespace(spot=24000.0),
        oi_strikes=[{"strike": 24000.0, "call_oi": 1, "put_oi": 2,
                     "call_ltp": 50.0, "put_ltp": 50.0, "net_gex": 3}],
    )
    assert lt._save_blast(data) == 1
    assert ts.load_strikes_day("2026-06-30", symbol="NIFTY", directory=dhan_dir).shape[0] == 1
    assert not (nse_dir / "NIFTY").exists()          # NSE tree untouched
