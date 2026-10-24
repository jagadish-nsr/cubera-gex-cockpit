"""Replay must recognise a real expiry day so the gamma-blast view shows (the snapshot is dated to
a synthetic REPLAY_DATE with a +2d synthetic expiry, so the live 'nearest==today' test can't work)."""

from data import session_flags as rp


def test_is_expiry_day_uses_feature_cache_when_covered(monkeypatch):
    import pandas as pd

    # a tiny fake feature cache: one 0-DTE day (dte 0.3) and one non-expiry (dte 5.4)
    fake = pd.DataFrame({"day": ["2026-07-07", "2026-07-06"], "dte": [0.3, 5.4]})
    monkeypatch.setattr("app.intraday_features.load_features", lambda store="NIFTY": fake)
    rp._expiry_cache.cache_clear()

    assert rp.is_expiry_day("NIFTY", "2026-07-07") is True     # dte < 0.6
    assert rp.is_expiry_day("NIFTY", "2026-07-06") is False    # covered, not flagged
    rp._expiry_cache.cache_clear()


def test_is_expiry_day_calendar_fallback_when_uncovered(monkeypatch):
    import pandas as pd

    monkeypatch.setattr("app.intraday_features.load_features",
                        lambda store="NIFTY": pd.DataFrame({"day": [], "dte": []}))
    rp._expiry_cache.cache_clear()
    # no cache coverage → weekly-calendar fallback (next weekly expiry on/after the day == the day)
    from datetime import date, timedelta

    from data.expiry import next_weekly_expiry
    exp = next_weekly_expiry(date(2026, 7, 20))
    non = exp + timedelta(days=1)
    assert rp.is_expiry_day("NIFTY", exp.isoformat()) is True
    assert rp.is_expiry_day("NIFTY", non.isoformat()) is False
    rp._expiry_cache.cache_clear()


def test_is_expiry_day_none_is_false():
    assert rp.is_expiry_day("NIFTY", None) is False
    assert rp.is_expiry_day("NIFTY", "") is False


def test_label_frame_true_vs_fake():
    """TRUE = a 0-DTE afternoon crossing reached a sustaining score (k_true set); FAKE = all pinned;
    morning-only / non-expiry crossings are excluded from the tag."""
    import pandas as pd

    aft = 13 * 60 + 45
    rows = pd.DataFrame({
        "day": ["2026-07-07", "2026-07-07", "2026-05-05", "2026-05-05", "2026-01-01", "2026-02-02"],
        "dte": [0.3, 0.3, 0.5, 0.5, 0.3, 1.4],     # last is the DAY-BEFORE (dte>0.6) → excluded
        "tod": [aft + 5, aft + 20, aft + 5, aft + 30, aft - 60, aft + 5],  # 5th is a MORNING cross
        "k_true": [2.0, float("nan"), float("nan"), float("nan"), 3.0, 2.0],
    })
    labels = rp._label_frame(rows)
    assert labels["2026-07-07"] == "TRUE"          # one afternoon cross reached TRUE
    assert labels["2026-05-05"] == "FAKE"          # afternoon crosses, none TRUE
    assert "2026-01-01" not in labels              # only a morning cross → unlabelled
    assert "2026-02-02" not in labels              # day-before-expiry (dte>0.6) → excluded


def test_blast_label_absent_is_none():
    assert rp.blast_label("NIFTY", None) is None
