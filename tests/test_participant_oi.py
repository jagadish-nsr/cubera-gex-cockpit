"""Hand-checkable tests for NSE participant-wise OI (fetch-agnostic: parse/derive/journal)."""

from datetime import date

import app.participant_oi as poi
from data.nse import parse_participant_oi

# Verified layout (2026-07-01): title line, header (note trailing spaces + the
# Call L / Put L / Call S / Put S option order), then Client/DII/FII/Pro/TOTAL rows.
# Numbers for Client are the real captured sample; others are illustrative.
_HEADER = ("Client Type,Future Index Long,Future Index Short,Future Stock Long,"
           "Future Stock Short,Option Index Call Long ,Option Index Put Long ,"
           "Option Index Call Short ,Option Index Put Short ,Option Stock Call Long,"
           "Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
           "Total Long Contracts,Total Short Contracts")
SAMPLE = "\n".join([
    '"Participant wise Open Interest as on Jul 01, 2026"',
    _HEADER,
    "Client,251066,63543,10,20,2163161,1888743,2028147,2586510,1,2,3,4,100,200",
    "DII,5000,1000,0,0,10,20,5,5,0,0,0,0,10,10",
    "FII,80000,20000,0,0,900000,700000,1200000,1400000,0,0,0,0,50,60",
    "Pro,3000,4000,0,0,500000,400000,700000,650000,0,0,0,0,30,40",
    "TOTAL,339066,88543,10,20,3563181,2988783,3928297,4636025,1,2,3,4,190,310",
])


def test_parse_maps_index_columns_in_verified_order():
    parsed = parse_participant_oi(SAMPLE)
    assert set(parsed) == {"Client", "DII", "FII", "Pro", "TOTAL"}
    c = parsed["Client"]
    # The easy-to-get-wrong bit: Call Long, Put Long, Call Short, Put Short.
    assert c["fut_long"] == 251066 and c["fut_short"] == 63543
    assert c["opt_call_long"] == 2163161 and c["opt_put_long"] == 1888743
    assert c["opt_call_short"] == 2028147 and c["opt_put_short"] == 2586510
    assert c["fut_stk_long"] == 10 and c["fut_stk_short"] == 20
    # Stock options/total columns are ignored (not in the dict).
    assert set(c) == {"fut_long", "fut_short", "opt_call_long", "opt_put_long",
                      "opt_call_short", "opt_put_short", "fut_stk_long", "fut_stk_short"}


def test_parse_rejects_malformed():
    assert parse_participant_oi("") is None
    assert parse_participant_oi("just one line") is None
    assert parse_participant_oi("title\nbad,header\nrow,1") is None   # missing index cols


def test_derive_signals_and_dealer_hint():
    sig = poi.derive_signals(parse_participant_oi(SAMPLE))
    per = sig["per_participant"]
    # net_fut = long - short
    assert per["Client"]["net_fut"] == 251066 - 63543
    assert sig["fii_net_fut"] == 80000 - 20000
    # net_opt_writing = (call_short+put_short) - (call_long+put_long)
    assert per["Client"]["net_opt_writing"] == (2028147 + 2586510) - (2163161 + 1888743)
    # Client writing = 562,753; FII writing = 1,000,000; Pro = 450,000 -> FII dominates.
    assert sig["top_option_writer"] == "FII"
    assert sig["dealer_sign_hint"] == "FII is the dominant index-option writer."


def test_dealer_hint_names_the_largest_writer_when_client_dominates():
    rows = "\n".join([
        "title",
        _HEADER,
        "Client,0,0,0,0,0,0,9000000,9000000,0,0,0,0,0,0",   # huge Client writing
        "FII,0,0,0,0,0,0,10,10,0,0,0,0,0,0",
        "Pro,0,0,0,0,0,0,5,5,0,0,0,0,0,0",
    ])
    sig = poi.derive_signals(parse_participant_oi(rows))
    assert sig["top_option_writer"] == "Client"
    assert sig["dealer_sign_hint"] == "Client is the dominant index-option writer."


def test_journal_roundtrip_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "PARTICIPANT_DIR", tmp_path / "participant_oi")
    parsed = parse_participant_oi(SAMPLE)
    p1 = poi.save_participant_day("2026-07-01", parsed)
    assert p1.exists() and poi.available_days() == ["2026-07-01"]
    back = poi.load_participant_day("2026-07-01")
    assert back["Client"]["opt_call_short"] == 2028147
    # Idempotent: a second save must not rewrite (return the same existing path).
    mtime = p1.stat().st_mtime_ns
    p2 = poi.save_participant_day("2026-07-01", {"Client": {"fut_long": 999}})
    assert p2 == p1 and p1.stat().st_mtime_ns == mtime      # unchanged
    assert poi.load_participant_day("2026-07-01")["Client"]["fut_long"] == 251066
    assert poi.latest_available()[0] == "2026-07-01"


def test_capture_latest_walks_back_on_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "PARTICIPANT_DIR", tmp_path / "participant_oi")

    # today's fetch returns None (not published), yesterday returns the sample.
    def fake_fetch(d):
        return SAMPLE if d == date(2026, 6, 30) else None

    got = poi.capture_latest("2026-07-01", fetch_fn=fake_fetch,
                             parse_fn=parse_participant_oi, max_back=4)
    assert got is not None
    date_ist, parsed = got
    assert date_ist == "2026-06-30"            # walked back one day
    assert parsed["FII"]["opt_call_short"] == 1200000
    assert poi.available_days() == ["2026-06-30"]   # cached


def test_capture_latest_uses_cache_without_fetching(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "PARTICIPANT_DIR", tmp_path / "participant_oi")
    poi.save_participant_day("2026-07-01", parse_participant_oi(SAMPLE))

    def boom(d):
        raise AssertionError("must not fetch when the day is cached")

    got = poi.capture_latest("2026-07-01", fetch_fn=boom, parse_fn=parse_participant_oi)
    assert got[0] == "2026-07-01"


def test_capture_latest_returns_none_when_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "PARTICIPANT_DIR", tmp_path / "participant_oi")
    got = poi.capture_latest("2026-07-01", fetch_fn=lambda d: None,
                             parse_fn=parse_participant_oi, max_back=3)
    assert got is None


def _save_days(tmp_path, monkeypatch, day_to_fut):
    """Write minimal FII-only participant files (one per date) with a given
    net-futures level, so participant_series/series_drift can be exercised."""
    monkeypatch.setattr(poi, "PARTICIPANT_DIR", tmp_path / "participant_oi")
    for d, fut_long in day_to_fut.items():
        poi.save_participant_day(d, {"FII": {
            "fut_long": fut_long, "fut_short": 0,
            "opt_call_long": 0, "opt_put_long": 0,
            "opt_call_short": 0, "opt_put_short": 0}})


def test_participant_series_chronological_and_last_n(tmp_path, monkeypatch):
    _save_days(tmp_path, monkeypatch,
               {"2026-06-29": 10, "2026-06-30": 20, "2026-07-01": 30, "2026-07-02": 40})
    ser = poi.participant_series("FII", n=3)
    assert [r["date"] for r in ser] == ["2026-06-30", "2026-07-01", "2026-07-02"]  # last 3
    assert [r["net_fut"] for r in ser] == [20, 30, 40]                            # long−short


def test_series_drift_rising_falling_flat():
    up = poi.series_drift([100, 200, 300, 400])
    assert up["trend"] == "rising" and up["drift"] > 0 and up["last"] == 400
    down = poi.series_drift([-100, -200, -300])          # FII adding shorts
    assert down["trend"] == "falling" and down["drift"] < 0
    flat = poi.series_drift([500, 500, 500])
    assert flat["trend"] == "flat"


def test_series_drift_handles_zero_crossing_and_short_input():
    # A signed series crossing zero: magnitude-based tolerance, not level-relative.
    cross = poi.series_drift([-300000, -100000, 100000, 300000])
    assert cross["trend"] == "rising"                    # getting less short -> bullish shift
    assert poi.series_drift([])["trend"] == "unknown"
    one = poi.series_drift([42])
    assert one["trend"] == "flat" and one["n"] == 1


def test_backfill_caches_available_days_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "PARTICIPANT_DIR", tmp_path / "participant_oi")
    # 02-Jul available, 01-Jul a "holiday" (None), 30-Jun available.
    served = {date(2026, 7, 2): SAMPLE, date(2026, 6, 30): SAMPLE}

    def fake_fetch(d):
        return served.get(d)

    got = poi.backfill(n_days=3, today_ist="2026-07-02",
                       fetch_fn=fake_fetch, parse_fn=parse_participant_oi)
    assert got == ["2026-06-30", "2026-07-02"]        # 01-Jul skipped (no file)
    # Idempotent: a second run captures nothing new (all cached).
    assert poi.backfill(n_days=3, today_ist="2026-07-02",
                        fetch_fn=fake_fetch, parse_fn=parse_participant_oi) == []


def test_net_calls_puts():
    v = {"opt_call_long": 100, "opt_call_short": 250,   # net short calls (bearish)
         "opt_put_long": 900, "opt_put_short": 400}     # net long puts (bearish)
    nc, npu = poi._net_calls_puts(v)
    assert nc == -150 and npu == 500
