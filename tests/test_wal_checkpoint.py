"""Health daemon PASSIVE-checkpoints sentinel.db-wal when it exceeds 25 MB."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest import mock

from app.admin import db
from app.admin.clock import IST
from app.admin.health_daemon import WAL_WARN_BYTES, _wal_alert, reset_gap_cache


def test_wal_alert_checkpoints_and_clears(tmp_path: Path):
    reset_gap_cache()
    db.set_db_path(tmp_path / "admin_ops.db")
    try:
        data = tmp_path / "data"
        data.mkdir()
        db_path = data / "sentinel.db"
        wal_path = data / "sentinel.db-wal"
        con = sqlite3.connect(str(db_path))
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t (id INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
        con.close()
        # Pretend WAL is oversized without needing a real 25 MB file.
        wal_path.write_bytes(b"x" * 100)
        stamp = datetime(2026, 11, 13, 12, 0, tzinfo=IST).isoformat()
        with mock.patch("app.admin.health_daemon.WAL_WARN_BYTES", 50), \
             mock.patch("app.admin.health_daemon._checkpoint_sentinel_wal") as ckpt:
            ckpt.return_value = "passive busy=0 log=1 ckpt=1"

            def _shrink(_db: Path) -> str:
                wal_path.write_bytes(b"")
                return "passive busy=0 log=0 ckpt=1"

            ckpt.side_effect = _shrink
            _wal_alert(tmp_path, stamp)
            assert ckpt.called
            assert db.list_alerts() == []
    finally:
        db.set_db_path(None)
        reset_gap_cache()


def test_wal_alert_keeps_warning_when_still_large(tmp_path: Path):
    reset_gap_cache()
    db.set_db_path(tmp_path / "admin_ops.db")
    try:
        data = tmp_path / "data"
        data.mkdir()
        (data / "sentinel.db").write_bytes(b"")
        wal = data / "sentinel.db-wal"
        wal.write_bytes(b"x" * 200)
        stamp = datetime(2026, 11, 13, 12, 0, tzinfo=IST).isoformat()
        with mock.patch("app.admin.health_daemon.WAL_WARN_BYTES", 50), \
             mock.patch(
                 "app.admin.health_daemon._checkpoint_sentinel_wal",
                 return_value="passive busy=1 log=10 ckpt=0",
             ):
            _wal_alert(tmp_path, stamp)
        rows = db.list_alerts()
        assert len(rows) == 1
        assert rows[0]["fingerprint"] == "DB|sentinel-wal"
        assert "PASSIVE" in rows[0]["description"] or "passive" in rows[0]["description"].lower()
    finally:
        db.set_db_path(None)
        reset_gap_cache()


def test_holiday_roll_raises_when_calendar_unavailable(monkeypatch):
    from data import expiry

    def boom(_d):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr("data.holidays.roll_back_to_trading_day", boom)
    from datetime import date
    import pytest
    with pytest.raises(RuntimeError, match="calendar unavailable"):
        expiry._holiday_roll(date(2026, 10, 27))
