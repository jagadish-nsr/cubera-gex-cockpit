"""CALL/PUT WATCH episode ledger — trigger stamp + kill/target resolution."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import json

from analytics.watch_episode import (
    derive_watch_levels,
    evaluate_watch_episode,
    open_watch_episode,
    resolve_episode_side,
    side_from_stance,
    _journal_path,
)

IST = timezone(timedelta(hours=5, minutes=30))


def test_derive_levels_call_uses_stretch_and_kill_text(tmp_path, monkeypatch):
    monkeypatch.setattr("analytics.watch_episode._journal_path", lambda: tmp_path)
    levels = derive_watch_levels(
        symbol="SENSEX",
        side="CALL",
        spot=82700,
        kill_text="Fail back below ₹82,500",
        stretch={"next_map_strike": 82800, "first_wall": 82500, "last_wall": 82700, "strike_step": 100},
        call_wall=82700,
        strike_step=100,
    )
    assert levels["kill_level"] == 82500.0
    assert levels["target_level"] == 82800.0


def test_open_and_resolve_target_hit(tmp_path, monkeypatch):
    monkeypatch.setattr("analytics.watch_episode._journal_path", lambda: tmp_path)
    now = datetime(2026, 11, 20, 10, 0, tzinfo=IST)
    ep = open_watch_episode(
        symbol="NIFTY",
        side="CALL",
        stance="CALL WATCH",
        spot=25500,
        now_ist=now,
        kill_text="Fail back below ₹25,450",
        stretch={"next_map_strike": 25550, "last_wall": 25500, "strike_step": 50},
        strike_step=50,
    )
    assert ep.status == "ACTIVE"
    assert "2026-11-20 10:00:00 IST" in ep.triggered_at
    assert ep.kill_level == 25450.0
    assert ep.target_level == 25550.0

    # Still active below target
    mid = evaluate_watch_episode(ep, spot=25520, now_ts=now.timestamp() + 60)
    assert mid.status == "ACTIVE"
    assert mid.mfe_pts == 20.0

    hit = evaluate_watch_episode(ep, spot=25555, now_ts=now.timestamp() + 120)
    assert hit.status == "TARGET_HIT"
    assert hit.resolved_at
    assert Path(tmp_path / f"{ep.episode_id}.json").exists()


def test_invalidate_on_kill(tmp_path, monkeypatch):
    monkeypatch.setattr("analytics.watch_episode._journal_path", lambda: tmp_path)
    now = datetime(2026, 11, 20, 14, 0, tzinfo=IST)
    ep = open_watch_episode(
        symbol="BANKNIFTY",
        side="CALL",
        stance="CALL WATCH",
        spot=64000,
        now_ist=now,
        kill_text="Fail back below ₹63,800",
        stretch={"next_map_strike": 64200, "last_wall": 64000},
        strike_step=100,
    )
    bad = evaluate_watch_episode(ep, spot=63750, now_ts=now.timestamp() + 300)
    assert bad.status == "INVALIDATED"
    assert bad.mae_pts < 0


def test_reuse_active_same_side_within_15m(tmp_path, monkeypatch):
    monkeypatch.setattr("analytics.watch_episode._journal_path", lambda: tmp_path)
    now = datetime(2026, 11, 20, 11, 0, tzinfo=IST)
    a = open_watch_episode(
        symbol="NIFTY", side="CALL", stance="CALL WATCH", spot=25600,
        now_ist=now, kill_text="Kill ₹25,500", stretch={"next_map_strike": 25650},
    )
    b = open_watch_episode(
        symbol="NIFTY", side="CALL", stance="CALL WATCH", spot=25610,
        now_ist=now + timedelta(minutes=5), kill_text="Kill ₹25,500",
        stretch={"next_map_strike": 25650},
    )
    assert a is not None and b is not None
    assert a.episode_id == b.episode_id


def test_side_follows_stance_not_map_side(tmp_path, monkeypatch):
    """23-Nov: CALL map migrations + PUT WATCH must not open CALL geometry."""
    monkeypatch.setattr("analytics.watch_episode._journal_path", lambda: tmp_path)
    assert side_from_stance("PUT WATCH") == "PUT"
    assert resolve_episode_side(stance="PUT WATCH", map_side="CALL") is None
    assert resolve_episode_side(stance="PUT WATCH", side="CALL") == "PUT"
    assert resolve_episode_side(stance="PUT WATCH", map_side="PUT") == "PUT"

    now = datetime(2026, 11, 23, 15, 20, tzinfo=IST)
    skipped = open_watch_episode(
        symbol="NIFTY",
        side="CALL",
        stance="PUT WATCH",
        spot=25366,
        now_ist=now,
        map_side="CALL",
        kill_text="Price gets back through ₹25,415",
        trigger_text="Hold the bearish tape idea; clear ₹25,350 if walls are split.",
        put_wall=25350,
        call_wall=25400,
        stretch={"next_map_strike": 25550, "last_wall": 25400},
    )
    assert skipped is None

    ep = open_watch_episode(
        symbol="NIFTY",
        side="CALL",  # stale arg — stance wins
        stance="PUT WATCH",
        spot=25366,
        now_ist=now,
        map_side="PUT",
        kill_text="Price gets back through ₹25,415",
        trigger_text="Hold below ₹25,350 with tape agreement.",
        put_wall=25350,
        call_wall=25400,
        stretch={"next_map_strike": 25300, "last_wall": 25350},
        strike_step=50,
    )
    assert ep is not None
    assert ep.side == "PUT"
    assert ep.stance == "PUT WATCH"
    assert ep.facts.get("map_side") == "PUT"
    # PUT geometry: target at or below spot
    assert ep.target_level <= ep.spot
