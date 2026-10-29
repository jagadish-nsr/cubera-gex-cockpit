"""Hand checks for the overnight FII clock and the anonymous futures tape."""
from datetime import date

from analytics.positioning_flow import (
    align_clocks,
    burst_notional,
    live_flow,
    option_book_confirms_short,
    overnight_positioning,
    tape_status,
    trailing_futures_change,
)
from app.session_reads import previous_session_date


def _fii(day, net, calls=100.0, puts=-100.0):
    return {"date": day, "net_fut": net, "net_calls": calls, "net_puts": puts}


def _fut(day, label):
    return {"date": day, "agg_buildup": label}


def _days():
    return [f"2026-10-{d:02d}" for d in (22, 23, 26, 27, 28)]


def test_option_mix_confirms_a_short_only_with_short_calls_or_long_puts():
    assert option_book_confirms_short(-10, 0) is True
    assert option_book_confirms_short(10, 5) is True
    assert option_book_confirms_short(10, -5) is False
    assert option_book_confirms_short(None, None) is None


def test_rising_fii_futures_and_long_buildup_is_accumulation():
    days = _days()
    fii = [_fii(day, 1000.0 * (i + 1)) for i, day in enumerate(days)]
    futures = [_fut(day, "long build-up") for day in days]
    out = overnight_positioning(fii, futures, symbol="NIFTY")
    assert out["state"] == "accumulation"
    assert out["file_date"] == "2026-10-28"
    assert out["long_days"] == 5
    assert "all NSE indices together" in out["line"]


def test_falling_short_with_short_calls_and_short_buildup_is_distribution():
    days = _days()
    fii = [_fii(day, -1000.0 * (i + 1), calls=-50.0, puts=20.0) for i, day in enumerate(days)]
    futures = [_fut(day, "short build-up") for day in days]
    out = overnight_positioning(fii, futures, symbol="BANKNIFTY")
    assert out["state"] == "distribution"
    assert "BANKNIFTY" in out["line"]


def test_unbacked_futures_short_is_a_hedge_not_distribution():
    days = _days()
    fii = [_fii(day, -5000.0 * (i + 1), calls=80.0, puts=-40.0) for i, day in enumerate(days)]
    futures = [_fut(day, "short build-up") for day in days]
    out = overnight_positioning(fii, futures, symbol="NIFTY")
    assert out["state"] == "hedge"
    assert "cash hedge" in out["line"]


def test_four_sessions_and_sensex_do_not_raise_a_named_flag():
    days = _days()[:4]
    short = overnight_positioning(
        [_fii(day, float(i)) for i, day in enumerate(days)],
        [_fut(day, "long build-up") for day in days],
    )
    assert short["state"] == "insufficient"
    sensex = overnight_positioning([], [], symbol="SENSEX")
    assert sensex["state"] == "not_on_exchange"
    assert "Sensex" in sensex["line"]


def test_trailing_window_prefers_the_last_half_hour_and_falls_back_to_the_session():
    trail = trailing_futures_change([
        {"time_ist": "10:00", "price": 100.0, "agg_oi": 1000.0},
        {"time_ist": "10:20", "price": 110.0, "agg_oi": 1100.0},
    ])
    assert trail["window"] == "Last 30 minutes"
    assert trail["dprice"] == 10.0
    assert trail["doi"] == 100.0
    session = trailing_futures_change([
        {"time_ist": "09:20", "price": 100.0, "agg_oi": 1000.0},
        {"time_ist": "15:20", "price": 90.0, "agg_oi": 900.0},
    ])
    assert session["window"] == "Session so far"
    assert session["dprice"] == -10.0


def test_bursts_inside_the_window_sum_by_aggressor_side():
    out = burst_notional([
        {"kind": "burst", "epoch": 1_000, "side": "buy", "notional": 5e7},
        {"kind": "burst", "epoch": 1_000 + 3600, "side": "sell", "notional": 2e7},
        {"kind": "print", "epoch": 1_000 + 3600, "side": "buy", "notional": 9e7},
    ])
    assert out["buy"] == 0.0
    assert out["sell"] == 2e7
    assert out["n"] == 1


def test_tape_confirms_only_when_cvd_and_bursts_share_the_buildup_side():
    assert tape_status("long build-up", 100.0, 5e7, 1e7) == "confirms"
    assert tape_status("long build-up", -100.0, 5e7, 1e7) == "opposes"
    assert tape_status("long build-up", 100.0, None, None) == "partial"
    assert tape_status("short covering", None, None, None) == "unavailable"


def test_alert_requires_a_fresh_file_and_a_confirming_tape():
    overnight = {"state": "accumulation"}
    live = {"label": "long build-up", "tape": "confirms"}
    assert align_clocks(overnight, live, file_is_previous_session=True)["alert"] is True
    stale = align_clocks(overnight, live, file_is_previous_session=False)
    assert stale["alert"] is False
    assert "older than the previous session" in stale["headline"]
    disagree = align_clocks(
        {"state": "accumulation"},
        {"label": "short build-up", "tape": "confirms"},
        file_is_previous_session=True,
    )
    assert disagree["alert"] is False
    assert "disagree" in disagree["headline"]
    hedge = align_clocks(
        {"state": "hedge"},
        {"label": "short build-up", "tape": "confirms"},
        file_is_previous_session=True,
    )
    assert hedge["alert"] is False
    assert "cash hedge" in hedge["headline"]
    split = align_clocks(
        {"state": "unconfirmed"},
        {"label": "short covering", "tape": "quiet"},
        file_is_previous_session=True,
    )
    assert split["alert"] is False
    assert "do not agree" in split["headline"]


def test_a_small_move_on_a_large_book_stays_flat():
    out = live_flow(
        dprice=1.0, doi=10.0, price_now=25000.0, oi_start=1_000_000.0,
        cvd_change=None, buy=None, sell=None, window="Last 30 minutes", symbol="NIFTY",
    )
    assert out["label"] == "flat"
    assert "no participant name" in out["line"]


def test_intraday_uses_the_last_captured_session_after_midnight(monkeypatch):
    from datetime import date
    import app.session_reads as reads

    def pick(symbol, day):
        if day == "2026-10-29":
            return [{"time_ist": "15:20", "price": 100.0, "agg_oi": 10.0}], "NSE"
        return [], None

    monkeypatch.setattr(reads, "_pick_intraday", pick)
    assert reads._session_with_intraday("NIFTY", date(2026, 10, 30)) == "2026-10-29"


def test_previous_session_skips_the_gandhi_jayanti_weekend():
    # 2026-10-02 is an NSE holiday (Friday). Monday 5 Oct looks back to Thursday 1 Oct.
    assert previous_session_date(date(2026, 10, 5)) == date(2026, 10, 1)
    assert previous_session_date(date(2026, 10, 26)) == date(2026, 10, 23)
