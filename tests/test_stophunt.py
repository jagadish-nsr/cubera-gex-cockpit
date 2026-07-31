"""Hand-checkable tests for app/stophunt.py — one fully-controlled synthetic sweep + guards."""
import pandas as pd

from app.stophunt import Config, Sweep, detect, study, _absorb


def _row(t, px, oi=1000, tb=50, ts=50):
    return {"epoch": t, "time_ist": f"09:{15 + t // 60:02d}:{t % 60:02d}", "symbol": "NIFTY",
            "roll": "near", "expiry": "2026-07-28", "ltp": float(px), "oi": oi,
            "tot_buy_qty": tb, "tot_sell_qty": ts}


def _synthetic():
    """Baseline flat at 101 (rolling max=101), then a single-step THRUST to 106 at t=140 (an up-sweep of
    the rolling high by +5), then a fade back to 99. OI falls into the sweep (unwind); book is supply-heavy."""
    rows = [_row(t, 101.0) for t in range(0, 140, 5)]              # t=0..135 baseline
    rows.append(_row(140, 106.0, oi=900, tb=40, ts=60))           # the sweep tick: OI −100, book_imb −0.2
    for t, px in [(145, 104.0), (150, 102.0), (155, 100.0), (160, 99.0)] + [(t, 99.0) for t in range(165, 205, 5)]:
        rows.append(_row(t, px))
    return pd.DataFrame(rows)


CFG = Config(lookback_min=1, margin=2, thrust_s=30, horizon_min=1, barrier=3, cooldown_s=1,
             round_step=0, or_min=0, level_types=("rolling",), n_placebo=50)


def test_detects_single_up_sweep_and_scores_outcome():
    sw = detect(_synthetic(), CFG)
    assert len(sw) == 1
    s = sw[0]
    assert s.direction == "up" and s.level_type == "rolling"
    assert s.level == 101.0 and s.price == 106.0 and s.thrust == 5.0     # breached rolling high by +5
    assert s.faded is True                                              # came back below 101 within 60s
    assert s.fwd == 7.0                                                 # 106 − 99 (oriented: fell after up-sweep)
    assert s.mfe == 7.0 and s.mae == 2.0                                # never traded above 106 post-sweep
    assert s.hit == 1                                                   # reverted ≥3 pts before continuing ≥3
    assert s.doi == -100.0                                              # OI unwound into the breach
    assert abs(s.book_imb - (-0.2)) < 1e-9 and _absorb(s)               # supply-heavy book = absorption


def test_no_sweep_when_move_is_slow():
    """Same 101→106 rise but spread over > thrust_s so it is a grind, not a thrust → no sweep."""
    rows = [_row(t, 101.0) for t in range(0, 100, 5)]
    for t, px in [(100, 102.0), (140, 103.0), (180, 104.0), (220, 105.0), (260, 106.0)]:
        rows.append(_row(t, px))
    rows += [_row(t, 106.0) for t in range(265, 320, 5)]
    assert detect(pd.DataFrame(rows), CFG) == []


def test_study_pools_and_gates_on_placebo():
    out = study([("d1", _synthetic())], CFG)
    assert out["n_days"] == 1
    assert out["overall"]["n"] == 1 and out["overall"]["fade_rate"] == 1.0
    assert "edge_vs_placebo" in out["overall"] and "t" in out["overall"]   # placebo baseline attached
    assert out["by_signature"]["oi_unwind"]["n"] == 1                      # the sweep is an unwind
    assert out["by_signature"]["oi_build"]["n"] == 0


def test_day_clustered_uses_days_not_sweeps():
    """The honest test treats each DAY as one observation. Two identical synthetic days → each day's
    edge is the same, all days positive, and n = 2 days (not 2 sweeps)."""
    out = study([("d1", _synthetic()), ("d2", _synthetic())], CFG)
    dc = out["day_clustered"]
    assert dc["n_days"] == 2 and dc["days_positive"] == 2       # both days lean positive
    assert dc["mean_daily_edge"] == dc["per_day"][0]["edge"]    # identical days → mean = each day's edge
    assert len(dc["per_day"]) == 2
    assert out["overall"]["n"] == 2                             # pooled still counts both sweeps


def test_empty_and_short_frames_are_safe():
    assert detect(pd.DataFrame([]), CFG) == []
    assert detect(pd.DataFrame([_row(0, 100.0)]), CFG) == []
    assert study([], CFG)["overall"] == {"n": 0}
