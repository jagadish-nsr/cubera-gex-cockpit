"""Feed recording + paper session summary — local paper only, never live."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ["CRYPTO_DB_URL"] = "sqlite:///:memory:"
os.environ.pop("CRYPTO_LIVE_FEEDS", None)
os.environ.pop("CRYPTO_SESSION_ID", None)

from crypto.config import load_config, set_flag
from crypto.engine.gex import compute_gex_features
from crypto.exec.ledger import TradeLedger
from crypto.providers.mock.chain import MockOptionChainProvider
from crypto.providers.mock.perp import MockPerpProvider
from crypto.providers.types import utcnow
from crypto.service import CockpitService
from crypto.store.db import dispose_engine, get_db_path, get_engine, init_db, store_status
from crypto.store.paths import REPO_ROOT, resolve_database_url, resolve_sqlite_path
from crypto.store.rotate import maybe_rotate_sqlite
from crypto.store.session import (
    build_summary,
    current_session,
    end_session,
    export_summary_csv,
    export_summary_json,
    reset_session_state,
    start_session,
)
from crypto.store.writer import StoreWriter


NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    db = tmp_path / "record.db"
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{db}")
    monkeypatch.delenv("CRYPTO_SESSION_ID", raising=False)
    monkeypatch.delenv("CRYPTO_RECORDING", raising=False)
    load_config(reload=True)
    dispose_engine()
    get_engine(reload=True, rotate=False)
    init_db(reload=False, rotate=False)
    # Flags after engine rebuild — get_engine must not wipe in-memory set_flag.
    set_flag("exec.paper", True, actor="test")
    set_flag("exec.confirm", True, actor="test")
    set_flag("exec.live", False, actor="test")
    set_flag("exec.auto", False, actor="test")
    reset_session_state()
    yield
    reset_session_state()
    dispose_engine()
    load_config(reload=True)


def test_relative_sqlite_path_resolves_to_repo_root():
    url = resolve_database_url("sqlite:///./data/crypto/cubera.db", root=REPO_ROOT)
    path = resolve_sqlite_path(url, root=REPO_ROOT)
    assert path is not None
    assert path.is_absolute()
    assert path == (REPO_ROOT / "data" / "crypto" / "cubera.db").resolve()
    assert url.startswith("sqlite:////") or url.startswith("sqlite:///")


def test_store_status_reports_path(tmp_path, monkeypatch):
    st = store_status()
    assert st["path"]
    assert Path(st["path"]).is_absolute()
    assert st["rotate_max_bytes"] > 0
    assert get_db_path() == Path(st["path"])


def test_rotate_when_oversized(tmp_path):
    db = tmp_path / "cubera.db"
    db.write_bytes(b"x" * 200)
    result = maybe_rotate_sqlite(db, max_bytes=100, keep=2)
    assert result["rotated"] is True
    assert result["archive"]
    assert not db.exists()
    assert Path(result["archive"]).exists()
    # Recreate and force another rotate → prune beyond keep
    db.write_bytes(b"y" * 200)
    maybe_rotate_sqlite(db, max_bytes=100, keep=1)
    archives = list(tmp_path.glob("cubera.*.db"))
    assert len(archives) == 1


def test_session_start_end_summary_counts():
    sess = start_session(session_id="ps_test_recording")
    assert sess["session_id"] == "ps_test_recording"
    assert sess["mode"] == "paper_confirm"
    assert sess["active"] is True
    assert current_session()["session_id"] == "ps_test_recording"

    writer = StoreWriter()
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    perp = asyncio.run(MockPerpProvider().fetch_perp())
    writer.write_chain(snap)
    gex = compute_gex_features(snap)
    writer.write_gex(gex)
    writer.write_orderflow(perp)
    writer.write_signal(
        {
            "status": "GO",
            "setup": "fade",
            "instrument": "BTC-PERPETUAL",
            "side": "sell",
            "score": 70,
            "veto_reasons": [],
            "created_at": utcnow(),
        }
    )
    writer.write_signal(
        {
            "status": "NO-GO",
            "setup": "",
            "instrument": "",
            "side": "",
            "score": 0,
            "veto_reasons": ["stale_chain", "news_blackout"],
            "created_at": utcnow(),
        }
    )

    ledger = TradeLedger()
    trade = ledger.record_submission(
        {
            "client_order_id": "cc_test_confirm_001",
            "venue": "paper",
            "adapter": "sim",
            "status": "sim_filled",
            "instrument": "BTC-PERPETUAL",
            "side": "sell",
            "setup": "fade",
            "entry": 100_000,
            "target": 99_000,
            "stop": 101_000,
            "size_usd": 500,
            "fill_price": 100_000,
            "realized_pnl_usd": 12.5,
            "mode": "paper_confirm",
            "submitted_at": utcnow(),
            "filled_at": utcnow(),
            "closed_at": utcnow(),
        }
    )
    assert trade["client_order_id"]

    summary = build_summary(session_id="ps_test_recording")
    assert summary["go_count"] >= 1
    assert summary["nogo_count"] >= 1
    assert summary["veto_signal_count"] >= 1
    assert "stale_chain" in summary["veto_tallies"]
    assert summary["confirms"] >= 1
    assert summary["realized_pnl_usd"] == 12.5
    assert summary["pnl_available"] is True
    assert summary["ticks"] >= 1
    assert summary["gex_features"] >= 1
    assert summary["exec_live"] is False

    raw_json = export_summary_json(session_id="ps_test_recording")
    parsed = json.loads(raw_json)
    assert parsed["session_id"] == "ps_test_recording"
    csv_body = export_summary_csv(session_id="ps_test_recording")
    assert "go_count" in csv_body.splitlines()[0]
    assert "ps_test_recording" in csv_body

    ended = end_session(session_id="ps_test_recording", write_summary=True)
    assert ended["ok"] is True
    assert ended["session"]["ended_at"]
    assert ended["summary"]["go_count"] >= 1


def test_cockpit_snapshot_persists_under_session():
    start_session(session_id="ps_snap")
    svc = CockpitService()
    data = asyncio.run(svc.snapshot())
    assert data["signal"]["status"] in {"GO", "NO-GO"}
    summary = build_summary(session_id="ps_snap")
    assert summary["signals_total"] >= 1
    assert summary["gex_features"] >= 1


def test_session_cli_summary(tmp_path, monkeypatch, capsys):
    start_session(session_id="ps_cli")
    StoreWriter().write_signal(
        {
            "status": "GO",
            "setup": "t",
            "instrument": "BTC",
            "side": "sell",
            "score": 1,
            "veto_reasons": [],
            "created_at": utcnow(),
        }
    )
    out = tmp_path / "summary.json"
    from crypto.session_cli import main

    rc = main(["summary", "--format", "json", "-o", str(out)])
    assert rc == 0
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["session_id"] == "ps_cli"
    assert payload["go_count"] >= 1


def test_api_session_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from crypto.server import app

    reset_session_state()
    start_session(session_id="ps_api")
    client = TestClient(app)
    # Avoid live feed startup side effects — TestClient triggers startup
    r = client.get("/api/session")
    assert r.status_code == 200
    body = r.json()
    assert body["recording_enabled"] is True
    assert body["session"]["session_id"]

    r2 = client.get("/api/session/summary")
    assert r2.status_code == 200
    assert "go_count" in r2.json()

    r3 = client.get("/api/session/export?format=csv")
    assert r3.status_code == 200
    assert "text/csv" in r3.headers.get("content-type", "")

    r4 = client.get("/api/store/status")
    assert r4.status_code == 200
    assert r4.json()["path"]

    r5 = client.get("/api/health")
    assert r5.status_code == 200
    assert "session_id" in r5.json()
