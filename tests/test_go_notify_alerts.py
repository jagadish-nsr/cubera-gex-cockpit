"""FIRE webhook helpers + position alert logic (Phase 2 polish)."""

from __future__ import annotations


def test_fire_fingerprint_stable():
    import web_server as ws

    d = {
        "id": "a1",
        "symbol": "NIFTY",
        "mode": "directional",
        "state": "FIRE",
        "strategy_key": "CONTINUATION_BULL",
        "score": 82.4,
    }
    fp1 = ws._go_fire_fingerprint(d)
    fp2 = ws._go_fire_fingerprint({**d, "id": "a2", "score": 82.1})
    assert fp1 == fp2
    assert "FIRE" in fp1


def test_format_fire_message_includes_evidence():
    import web_server as ws

    msg = ws._go_format_fire_message({
        "score": 82,
        "symbol": "NIFTY",
        "mode": "directional",
        "strategy_key": "CONTINUATION_BULL",
        "enter_rule": "NOW_CLOSE",
        "size_tier": "FULL",
        "thesis": "Buyer CVD",
        "contracts": [{"action": "BUY", "opt_type": "CE", "strike": 25500, "ltp": 72}],
        "context": {"oi_ts": "10:42", "lot_size": 65},
        "evidence": [
            {"label": "Sentinel", "value": 82, "pass": True, "factor": "sentinel"},
            {"label": "CVD", "value": True, "pass": True, "factor": "cvd"},
        ],
    })
    assert "FIRE 82" in msg
    assert "BUY CE 25500" in msg
    assert "PASS" in msg
    assert "confirm at broker" in msg


def test_maybe_notify_respects_prefs(monkeypatch):
    import web_server as ws

    ws._GO_PREFS["fire_webhook"] = False
    ws._GO_LAST_FIRE_NOTIFY.clear()
    assert ws._go_maybe_notify_fire({"state": "FIRE", "id": "x", "score": 80, "symbol": "NIFTY", "mode": "directional", "strategy_key": "A"}) is None

    ws._GO_PREFS["fire_webhook"] = True
    ws._GO_PREFS["webhook_url"] = ""
    assert ws._go_maybe_notify_fire({"state": "FIRE", "id": "x", "score": 80, "symbol": "NIFTY", "mode": "directional", "strategy_key": "A"}) is None

    calls = []

    class _Resp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        return _Resp()

    monkeypatch.setattr("requests.post", fake_post)
    ws._GO_PREFS["webhook_url"] = "https://example.test/hook"
    ws._GO_LAST_FIRE_NOTIFY.clear()
    decision = {
        "id": "d1",
        "state": "FIRE",
        "score": 88,
        "symbol": "NIFTY",
        "mode": "directional",
        "strategy_key": "CONTINUATION_BULL",
        "enter_rule": "NOW_CLOSE",
        "size_tier": "FULL",
        "thesis": "t",
        "contracts": [],
        "evidence": [],
        "context": {},
    }
    out = ws._go_maybe_notify_fire(decision)
    assert out["status"] == "sent"
    assert len(calls) == 1
    # dedupe second emit same fingerprint
    out2 = ws._go_maybe_notify_fire({**decision, "id": "d2"})
    assert out2["status"] == "deduped"
    assert len(calls) == 1


def test_position_alerts_credit_and_wall(tmp_path, monkeypatch):
    import data.journal.go_efficiency as ge
    import web_server as ws

    monkeypatch.setattr(ge, "_DB_PATH", tmp_path / "go_efficiency.db")
    ge.ensure_schema()

    credit_d = {
        "id": "alert_ic",
        "symbol": "NIFTY",
        "mode": "structure",
        "state": "FIRE",
        "score": 80,
        "strategy_key": "IRON_CONDOR",
        "contracts": [],
        "risk": {"credit_or_debit": 40},
        "context": {"spot": 25400},
    }
    ge.record_event("STAGE", credit_d, trade_id="t2")
    # entry 40 credit; mark 15 → 62.5% captured
    ge.mark_open("alert_ic", mark_premium=15.0)

    levels = type("L", (), {"spot": 25610.0, "call_wall": 25600.0, "put_wall": 25200.0})()
    dash = type("D", (), {"levels": levels, "contracts": None})()
    alerts = ws._go_position_alerts("NIFTY", dash)
    kinds = {a["kind"] for a in alerts}
    assert "credit_50pct" in kinds
    assert "wall_threat" in kinds


def test_position_alerts_trail_arm(monkeypatch):
    import web_server as ws

    monkeypatch.setattr(
        "data.journal.go_efficiency.list_open",
        lambda symbol=None, include_decision=False: [{
            "decision_id": "d_arm",
            "mode": "directional",
            "entry_premium": -72.0,
            "mark_premium": -94.0,
            "trail_armed": False,
            "trail_stop": None,
            "mfe": 22.0,
            "mae": 0.0,
        }],
    )
    alerts = ws._go_position_alerts("NIFTY", None)
    assert any(a["kind"] == "trail_arm" for a in alerts)
