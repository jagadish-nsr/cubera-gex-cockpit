"""Scoring logic for the dealer-sign behavioural test (offline, synthetic rows)."""

from scripts.score_sessions import pair_by_day, score, summarise, verdict


def _row(date, ts, spot, regime, pw, cw):
    return {"date_ist": date, "timestamp_utc": ts, "spot": str(spot),
            "regime": regime, "mode_label": "", "put_wall": str(pw), "call_wall": str(cw)}


def _two_snap_day(date, open_spot, pm_spot, regime, pw, cw):
    return [_row(date, f"{date}T04:00:00Z", open_spot, regime, pw, cw),
            _row(date, f"{date}T10:00:00Z", pm_spot, regime, pw, cw)]


def test_pairing_picks_open_and_afternoon():
    rows = _two_snap_day("2026-06-01", 23500, 23560, "POSITIVE", 23400, 23600)
    (d, op, aft), = pair_by_day(rows)
    assert d == "2026-06-01"
    assert op["timestamp_utc"].endswith("04:00:00Z")     # open = earliest
    assert aft["timestamp_utc"].endswith("10:00:00Z")     # afternoon = latest


def test_in_range_detection():
    rows = _two_snap_day("2026-06-01", 23500, 23560, "POSITIVE", 23400, 23600)  # 3pm inside
    rows += _two_snap_day("2026-06-02", 23500, 23720, "POSITIVE", 23400, 23600)  # 3pm above cw
    recs = score(rows)
    by = {r["date"]: r for r in recs}
    assert by["2026-06-01"]["in_range"] is True
    assert by["2026-06-02"]["in_range"] is False
    assert by["2026-06-02"]["move"] == 220.0


def test_summary_and_verdict_pinning():
    # 6 positive days, 5 pin inside, 1 escapes -> pin rate 5/6 ~ 0.83 -> consistent
    rows = []
    for i in range(5):
        rows += _two_snap_day(f"2026-06-0{i+1}", 23500, 23550, "POSITIVE", 23400, 23600)
    rows += _two_snap_day("2026-06-06", 23500, 23900, "POSITIVE", 23400, 23600)  # escapes
    s = summarise(score(rows))
    assert s["pos_days"] == 6
    assert abs(s["pos_pin_rate"] - 5 / 6) < 1e-9
    assert "CONSISTENT" in verdict(s)


def test_verdict_insufficient_data():
    rows = _two_snap_day("2026-06-01", 23500, 23550, "POSITIVE", 23400, 23600)
    assert "Not enough" in verdict(summarise(score(rows)))
