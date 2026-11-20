"""CALL/PUT WATCH episode ledger — trigger stamp + kill/target resolution."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import json

from analytics.watch_episode import (
    derive_watch_levels,
    evaluate_watch_episode,
    open_watch_episode,
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
    assert a.episode_id == b.episode_id
