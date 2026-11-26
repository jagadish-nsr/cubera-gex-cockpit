"""26-Nov: evidence stats, prior-session sweep, resolved-only effectiveness rates.

Every expected number below is small enough to check by hand.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

IST = timezone(timedelta(hours=5, minutes=30))
CLIENT_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "cockpit.js"


@pytest.fixture
def rs(tmp_path, monkeypatch):
    import data.journal.recommendations_store as rs_mod

    monkeypatch.setattr(rs_mod, "_DB_PATH", tmp_path / "recs.db")
    monkeypatch.setattr(rs_mod, "_PRIOR_SWEEP_DAY", None)
    monkeypatch.setattr(rs_mod, "_REC_SESSION_END_DATE", None)
    rs_mod._SCHEMA_READY_PATHS.clear()
    rs_mod._EFFECTIVENESS_CACHE.clear()
    rs_mod.ensure_schema()
    return rs_mod


def _insert_rec(rs, rec_id, *, tier="HIGH", play="MOMENTUM", symbol="NIFTY",
                status="ACTIVE", triggered="2026-11-20 10:00:00", last_appeared=None):
    with rs._get_write_conn() as conn:
        conn.execute(
            """
            INSERT INTO recommendation_records (
                rec_id, symbol, side, strike, option_symbol, play_type, play_badge,
                triggered_at, initial_spot, initial_ltp, initial_score, current_score,
                conviction_tier, target_1, target_1_spot, target_2, target_2_spot,
                stop_loss, spot_invalidation, spot_invalidation_text, risk_reward_ratio,
                entry_trigger, rationale, catalyst_tags_json, status, payload_json, last_appeared
            ) VALUES (?, ?, 'CE', 25800, 'X', 'MOMENTUM', ?, ?, 25800, 120, 80, 80,
                      ?, 150, 25900, 180, 26000, 90, 25700, 'Below', '1:2',
                      'Break', 'Test', '[]', ?, '{}', ?)
            """,
            (rec_id, symbol, play, triggered, tier, status, last_appeared),
        )
        conn.commit()


def _insert_sentinel(rs, rec_id, *, symbol="BANKNIFTY", status="TARGET_HIT",
                     entry=100.0, exit_price=150.0, signal="CONTINUATION_BULL"):
    with rs._get_write_conn() as conn:
        conn.execute(
            """
            INSERT INTO sentinel_trade_recommendations (
                rec_id, symbol, strike, side, option_symbol, triggered_at, status,
                spot_entry, spot_target, spot_invalidation, option_entry, option_target,
                option_sl, contributing_signals_json, exit_price, latest_signal_type
            ) VALUES (?, ?, 57000, 'CE', 'BN57000CE', '2026-11-20 10:00:00', ?,
                      57000, 57100, 56900, ?, ?, ?, '[]', ?, ?)
            """,
            (rec_id, symbol, status, entry, entry + 50, entry - 40, exit_price, signal),
        )
        conn.commit()


# --- pure summaries ---------------------------------------------------------

def test_tier_inversion_flagged_only_with_enough_samples(rs):
    # HIGH: 5 wins / 30 -> 16.7%. LOW: 15 wins / 30 -> 50.0%. Inverted.
    rows = [{"tier": "HIGH", "play": "P", "symbol": "NIFTY", "status": "TGT1_HIT" if i < 5 else "SL_HIT"}
            for i in range(30)]
    rows += [{"tier": "LOW", "play": "P", "symbol": "NIFTY", "status": "TGT2_HIT" if i < 15 else "INVALIDATED"}
             for i in range(30)]
    ev = rs.summarize_recommendation_evidence(rows)
    assert ev["resolved_n"] == 60
    assert ev["win_rate"] == 33.3  # 20 wins / 60
    assert ev["by_tier"]["HIGH"] == {"n": 30, "win_rate": 16.7}
    assert ev["by_tier"]["LOW"] == {"n": 30, "win_rate": 50.0}
    assert ev["tier_inverted"] is True
    assert "underperforming" in ev["verdict"]

    # Same HIGH rate but only 10 HIGH rows: below min_n, so no inversion call.
    thin = [r for r in rows if r["tier"] == "LOW"] + rows[:10]
    ev_thin = rs.summarize_recommendation_evidence(thin)
    assert ev_thin["tier_inverted"] is False
    assert "Too few" in ev_thin["verdict"]


def test_tier_ordering_holds_message(rs):
    rows = [{"tier": "HIGH", "play": "P", "symbol": "N", "status": "TGT1_HIT"} for _ in range(30)]
    rows += [{"tier": "LOW", "play": "P", "symbol": "N", "status": "SL_HIT"} for _ in range(30)]
    ev = rs.summarize_recommendation_evidence(rows)
    assert ev["tier_inverted"] is False
    assert "holds" in ev["verdict"]


def test_sentinel_evidence_excludes_untagged_replay_and_uses_points(rs):
    rows = [
        # live: (150-100)=+50 TARGET_HIT, (60-100)=-40 SPOT_INVAL
        {"symbol": "BANKNIFTY", "latest_signal_type": "CVD_DIVERGENCE_BULL", "status": "TARGET_HIT",
         "option_entry": 100.0, "exit_price": 150.0},
        {"symbol": "NIFTY", "latest_signal_type": "ABSORPTION_BEAR", "status": "SPOT_INVAL",
         "option_entry": 100.0, "exit_price": 60.0},
        # replay: no signal type, must be excluded even though it is a win
        {"symbol": "SENSEX", "latest_signal_type": None, "status": "TARGET_HIT",
         "option_entry": 100.0, "exit_price": 300.0},
    ]
    ev = rs.summarize_sentinel_evidence(rows)
    assert ev["resolved_n"] == 2
    assert ev["excluded_untagged"] == 1
    assert ev["hit_rate"] == 50.0
    assert ev["mean_pts"] == 5.0  # (50 - 40) / 2
    assert ev["median_pts"] == 5.0
    assert ev["by_symbol"]["BANKNIFTY"] == {"n": 1, "hit_rate": 100.0, "mean_pts": 50.0}
    assert ev["by_symbol"]["NIFTY"] == {"n": 1, "hit_rate": 0.0, "mean_pts": -40.0}
    assert "SENSEX" not in ev["by_symbol"]


# --- prior-session sweep ----------------------------------------------------

def test_sweep_expires_only_rows_not_seen_this_session(rs):
    _insert_rec(rs, "old", last_appeared="2026-11-20 14:00:00")          # last seen Thu
    _insert_rec(rs, "seen_today", last_appeared="2026-11-26 09:30:00")  # re-seen today
    _insert_rec(rs, "already_done", status="TGT1_HIT", last_appeared="2026-11-20 14:00:00")

    now = datetime(2026, 11, 26, 10, 0, tzinfo=IST)
    assert rs.sweep_prior_session_active(now=now) == 1

    with rs._get_read_conn() as conn:
        status = {r["rec_id"]: r["status"] for r in conn.execute("SELECT rec_id, status FROM recommendation_records")}
    assert status == {"old": "EXPIRED", "seen_today": "ACTIVE", "already_done": "TGT1_HIT"}


def test_sweep_runs_once_per_day_unless_forced(rs):
    _insert_rec(rs, "old", last_appeared="2026-11-20 14:00:00")
    now = datetime(2026, 11, 26, 10, 0, tzinfo=IST)
    assert rs.sweep_prior_session_active(now=now) == 1
    _insert_rec(rs, "older", last_appeared="2026-11-21 14:00:00")
    assert rs.sweep_prior_session_active(now=now) == 0  # guard: already ran today
    assert rs.sweep_prior_session_active(now=now, force=True) == 1


def test_sweep_leaves_sentinel_rows_alone(rs):
    _insert_sentinel(rs, "s1", status="TARGET_HIT")
    with rs._get_write_conn() as conn:
        conn.execute("UPDATE sentinel_trade_recommendations SET status='ACTIVE', created_at='2026-11-01 10:00:00'")
        conn.commit()
    rs.sweep_prior_session_active(now=datetime(2026, 11, 26, 10, 0, tzinfo=IST), force=True)
    with rs._get_read_conn() as conn:
        assert conn.execute("SELECT status FROM sentinel_trade_recommendations").fetchone()[0] == "ACTIVE"


# --- DB-backed evidence and effectiveness -----------------------------------

def test_get_recommendation_evidence_counts_only_resolved(rs):
    _insert_rec(rs, "w1", tier="LOW", status="TGT1_HIT")
    _insert_rec(rs, "l1", tier="LOW", status="SL_HIT")
    _insert_rec(rs, "open", tier="LOW", status="ACTIVE")
    _insert_rec(rs, "gone", tier="LOW", status="EXPIRED")
    _insert_sentinel(rs, "s_win", status="TARGET_HIT", entry=100, exit_price=130)
    _insert_sentinel(rs, "s_replay", status="TARGET_HIT", entry=100, exit_price=500, signal=None)

    ev = rs.get_recommendation_evidence()
    assert ev["trade_recs"]["resolved_n"] == 2
    assert ev["trade_recs"]["win_rate"] == 50.0
    assert ev["sentinel"]["resolved_n"] == 1
    assert ev["sentinel"]["excluded_untagged"] == 1
    assert ev["sentinel"]["mean_pts"] == 30.0


def test_effectiveness_rates_use_resolved_denominator(rs):
    # 1 TGT1 + 1 SL resolved, 8 ACTIVE open. Old code gave 10% target rate; resolved-only gives 50%.
    _insert_rec(rs, "t", play="MOMENTUM", status="TGT1_HIT")
    _insert_rec(rs, "s", play="MOMENTUM", status="SL_HIT")
    for i in range(8):
        _insert_rec(rs, f"a{i}", play="MOMENTUM", status="ACTIVE")
    metrics = {m["badge"]: m for m in rs.get_strategy_effectiveness_metrics("ALL")}
    mom = metrics["MOMENTUM"]
    assert mom["total"] == 2
    assert mom["open"] == 8
    assert mom["t1_rate"] == 50.0
    assert mom["sl_rate"] == 50.0

    summary = rs.summarize_effectiveness([mom])
    assert summary["target_hit_rate"] == 50.0
    assert summary["open_setups"] == 8


# --- static wiring ----------------------------------------------------------

def test_frontend_wires_evidence_strip_and_chips():
    js = CLIENT_JS.read_text(encoding="utf-8")
    assert "function renderRecEvidence(" in js
    assert "renderRecEvidence(data.evidence);" in js
    assert "evidenceChipsForIdea(best)" in js
    assert "sentinel-evidence-line" in js
