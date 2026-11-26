"""Session-start contract: the core paths that must hold when the 09:07 ingest arms.

Each test pins a failure seen live (dates in the docstrings). promote.sh and CI run this
file; a change that breaks one of these breaks the session, so it does not ship.

Hand-check numbers:
  /optionchain budget  = 60 / 3.1            = 19.35 calls/min
  steady chain demand  = 3 indices x 2/min   = 6 calls/min   (one expiry each, list cached)
  expiry-day blast     = 60 / 8s             = 7.5 calls/min
  total on expiry day  = 13.5 <= 19.35
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import pickle
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from data import dhan_live

ROOT = Path(__file__).resolve().parents[1]
IST = timezone(timedelta(hours=5, minutes=30))


# ── Instrument invariants (CLAUDE.md) ─────────────────────────────────────────────────────────

def test_lot_sizes_and_sign_model_are_the_confirmed_ones():
    assert config.get_index("NIFTY").lot_size == 65
    assert config.get_index("BANKNIFTY").lot_size == 30
    assert config.get_index("SENSEX").lot_size == 20
    assert config.DEALER_SIGN_MODEL == "short_call_short_put"


# ── Dhan /optionchain budget ──────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, ok=True, status=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status

    def json(self):
        return self._payload


@pytest.fixture
def chain_calls(monkeypatch, tmp_path):
    """Count Dhan POSTs by path; isolate caches, gate dir and the owner env."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(dhan_live, "_ist_today", lambda: "2026-11-26")
    monkeypatch.setattr(dhan_live, "_EXP_CACHE", {})
    monkeypatch.setattr(dhan_live, "_GATE_DIR", tmp_path / "gate")
    monkeypatch.setattr(dhan_live, "_CROSS_PROCESS_GATE", False)
    monkeypatch.delenv("CUBERA_DHAN_CHAIN_OWNER", raising=False)
    monkeypatch.delenv("CUBERA_DHAN_CHAIN_YIELD", raising=False)
    calls: list[str] = []

    def _post(path, body, timeout=30, hi=False):
        calls.append(path)
        if path.endswith("/expirylist"):
            return _Resp({"data": ["2026-12-02", "2026-11-26", "2026-12-09"]})
        return _Resp({"data": {}})

    monkeypatch.setattr(dhan_live, "_post", _post)
    monkeypatch.setattr(dhan_live, "_to_nse_rows", lambda js, e, lot_size=1: ([{"e": e}], 100.0))
    return calls


def test_expiry_list_is_fetched_once_per_session_day(chain_calls):
    """26 Nov: every chain pull re-asked for the expiry list (calls=2 for one expiry)."""
    for _ in range(5):
        assert dhan_live.fetch_expiry_list("SENSEX") == ["2026-11-26", "2026-12-02", "2026-12-09"]
    assert chain_calls.count("/optionchain/expirylist") == 1


def test_expiry_list_refresh_failure_keeps_the_live_part_of_the_last_list(chain_calls, monkeypatch):
    dhan_live._EXP_CACHE["NIFTY"] = ("2026-11-26", 0.0, ["2026-11-24", "2026-11-26", "2026-12-01"])
    monkeypatch.setattr(dhan_live, "_post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert dhan_live.fetch_expiry_list("NIFTY") == ["2026-11-26", "2026-12-01"]


def test_chain_demand_per_cycle_fits_the_rate_budget(chain_calls):
    """Two 30s cycles x 3 indices, one expiry each: 3 list calls once, then 3 chain calls/cycle."""
    for _ in range(2):
        for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
            dhan_live.fetch_chain(sym, max_expiries=1)
    assert chain_calls.count("/optionchain/expirylist") == 3
    assert chain_calls.count("/optionchain") == 6

    import web_server
    per_min_budget = 60.0 / dhan_live._MIN_INTERVAL
    steady = 3 * (60.0 / web_server._CHAIN_INTERVAL_S)
    blast = 60.0 / 8.0
    assert steady + blast <= per_min_budget


# ── One process owns the chain quota in session ──────────────────────────────────────────────

@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(dhan_live, "_GATE_DIR", tmp_path / "gate")
    monkeypatch.setattr(dhan_live, "_CROSS_PROCESS_GATE", True)
    monkeypatch.setattr(dhan_live, "_owner_last_write", [0.0])
    monkeypatch.delenv("CUBERA_DHAN_CHAIN_OWNER", raising=False)
    monkeypatch.delenv("CUBERA_DHAN_CHAIN_YIELD", raising=False)
    return tmp_path / "gate"


def _write_lease(gate_dir: Path, pid: int, ts: float, root: str = "/prod") -> None:
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "chain.owner").write_text(json.dumps({"pid": pid, "root": root, "ts": ts}))


def test_owner_writes_lease_and_never_yields(gate, monkeypatch):
    monkeypatch.setenv("CUBERA_DHAN_CHAIN_OWNER", "1")
    assert dhan_live.write_chain_owner_lease(force=True) is True
    lease = json.loads((gate / "chain.owner").read_text())
    assert lease["pid"] == os.getpid()
    assert dhan_live.chain_yield_reason(session_open=True) == ""


def test_non_owner_yields_in_session_while_owner_lease_is_fresh(gate, monkeypatch):
    monkeypatch.setattr(dhan_live, "_pid_alive", lambda pid: True)
    _write_lease(gate, pid=os.getpid() + 1, ts=time.time())
    reason = dhan_live.chain_yield_reason(session_open=True)
    assert "chain quota owned by ingest" in reason


def test_non_owner_post_raises_before_touching_the_network(gate, monkeypatch):
    monkeypatch.setattr(dhan_live, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(config, "is_chain_ingest_open", lambda *a, **k: True)
    _write_lease(gate, pid=os.getpid() + 1, ts=time.time())
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("network call while yielding"))
    with pytest.raises(dhan_live.DhanChainYield):
        dhan_live._post("/optionchain", {"UnderlyingScrip": 13})


@pytest.mark.parametrize("case", ["off_session", "override", "stale", "dead_pid", "own_pid", "no_gate"])
def test_non_owner_may_fetch_when_no_live_owner_or_off_session(gate, monkeypatch, case):
    alive = case != "dead_pid"
    monkeypatch.setattr(dhan_live, "_pid_alive", lambda pid: alive)
    pid = os.getpid() if case == "own_pid" else os.getpid() + 1
    ts = time.time() - (dhan_live._OWNER_LEASE_S + 5) if case == "stale" else time.time()
    _write_lease(gate, pid=pid, ts=ts)
    if case == "override":
        monkeypatch.setenv("CUBERA_DHAN_CHAIN_YIELD", "0")
    if case == "no_gate":
        monkeypatch.setattr(dhan_live, "_CROSS_PROCESS_GATE", False)
    session_open = case != "off_session"
    assert dhan_live.chain_yield_reason(session_open=session_open) == ""


def test_yield_is_not_swallowed_into_a_generic_chain_error(chain_calls, monkeypatch):
    """A swallowed yield became "no data" and sent the process to the NSE scrape."""
    def _yield(*a, **k):
        raise dhan_live.DhanChainYield("owned")
    monkeypatch.setattr(dhan_live, "_post", _yield)
    dhan_live._EXP_CACHE["NIFTY"] = ("2026-11-26", time.time(), ["2026-11-26"])
    with pytest.raises(dhan_live.DhanChainYield):
        dhan_live.fetch_chain("NIFTY", max_expiries=1)


def test_yielding_process_does_not_fall_back_to_nse(monkeypatch):
    from app import data_access
    import data.dhan as dhan_mod
    import data.nse as nse_mod
    monkeypatch.setattr(dhan_mod, "token_status", lambda: {"present": True})

    def _yield(*a, **k):
        raise dhan_live.DhanChainYield("owned")
    monkeypatch.setattr(dhan_live, "fetch_chain", _yield)
    monkeypatch.setattr(nse_mod, "fetch_chain", lambda *a, **k: pytest.fail("NSE scrape while yielding"))
    with pytest.raises(dhan_live.DhanChainYield):
        data_access._fetch_live_chain(config.get_index("NIFTY"), "dhan", max_expiries=1)


def test_heartbeat_refreshes_the_owner_lease(gate, monkeypatch, tmp_path):
    """The breaker can hold the chain loop idle; the lease must not lapse meanwhile."""
    from app import ingest_heartbeat
    monkeypatch.setenv("CUBERA_DHAN_CHAIN_OWNER", "1")
    monkeypatch.setattr(ingest_heartbeat, "heartbeat_path", lambda: tmp_path / "hb.json")
    ingest_heartbeat.write_beat(role="ingest")
    assert (gate / "chain.owner").is_file()


def test_prod_ingest_is_the_declared_owner():
    assert 'setdefault("CUBERA_DHAN_CHAIN_OWNER", "1")' in (ROOT / "scripts" / "run_ingest.py").read_text()
    assert "CUBERA_DHAN_CHAIN_OWNER=1" in (ROOT / "run_prod.sh").read_text()


def _publish(root: Path, symbol: str, fetched_at: datetime, source: str = "dhan") -> object:
    bundle = SimpleNamespace(levels=SimpleNamespace(spot=84400.0), source=source, symbol=symbol)
    path = root / "data" / "journal" / f"latest_snap_{symbol}.pkl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump({"data": bundle, "fetched_at": fetched_at}, fh)
    return bundle


def test_yielding_process_reads_the_owner_published_book(gate, monkeypatch, tmp_path):
    from app import data_access
    prod = tmp_path / "prod"
    monkeypatch.setattr(dhan_live, "_pid_alive", lambda pid: True)
    _write_lease(gate, pid=os.getpid() + 1, ts=time.time(), root=str(prod))
    _publish(prod, "SENSEX", datetime.now(timezone.utc) - timedelta(seconds=20))
    got = data_access._owner_published_book("SENSEX")
    assert got is not None and got.levels.spot == 84400.0
    _publish(prod, "SENSEX", datetime.now(timezone.utc) - timedelta(seconds=600))
    assert data_access._owner_published_book("SENSEX") is None


def test_yield_serves_owner_book_without_tripping_the_breaker(monkeypatch):
    from app import data_access
    from resilience import get_circuit_breaker
    import data.news as news_mod
    import data.sectors as sectors_mod
    import data.nse as nse_mod
    monkeypatch.setattr(news_mod, "fetch_news", lambda *a, **k: None)
    monkeypatch.setattr(sectors_mod, "fetch_sector_snap", lambda *a, **k: None)
    monkeypatch.setattr(nse_mod, "fetch_india_vix", lambda *a, **k: None)
    failed: cf.Future = cf.Future()
    failed.set_exception(dhan_live.DhanChainYield("owned"))
    monkeypatch.setattr(data_access, "_submit_chain_once", lambda *a, **k: failed)
    owner_book = SimpleNamespace(levels=SimpleNamespace(spot=25800.0))
    monkeypatch.setattr(data_access, "_owner_published_book", lambda sym, *a, **k: owner_book)
    cb = get_circuit_breaker("NIFTY")
    before = cb.consecutive_failures
    got = data_access.load_dashboard(symbol="NIFTY", window="current_week")
    assert got is owner_book
    assert cb.consecutive_failures == before


# ── Single-flight chain fetch ─────────────────────────────────────────────────────────────────

def test_timed_out_chain_fetch_is_joined_not_requeued(monkeypatch):
    """26 Nov: a 35s timeout left the worker at the gate and the next cycle queued another."""
    from app import data_access
    release = threading.Event()
    started: list[str] = []

    def _slow(idx, live_source, max_expiries=None, force_live=False, submitted_at=None):
        started.append(idx.symbol)
        release.wait(5)
        return ("payload", "dhan", None, None)

    monkeypatch.setattr(data_access, "_fetch_live_chain_timed", _slow)
    monkeypatch.setattr(data_access, "_INFLIGHT", {})
    idx = config.get_index("NIFTY")
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        f1 = data_access._submit_chain_once(ex, idx, "dhan", 1, False)
        f2 = data_access._submit_chain_once(ex, idx, "dhan", 1, False)
        assert f1 is f2
        other = data_access._submit_chain_once(ex, config.get_index("SENSEX"), "dhan", 1, False)
        assert other is not f1
        release.set()
        assert f1.result(5)[1] == "dhan"
        other.result(5)
        f3 = data_access._submit_chain_once(ex, idx, "dhan", 1, False)
        f3.result(5)
        assert f3 is not f1
    assert started.count("NIFTY") == 2 and started.count("SENSEX") == 1


# ── Timeseries keeps writing on an expiry afternoon ───────────────────────────────────────────

def test_once_fire_releases_its_lock_before_the_blast_loop(monkeypatch, tmp_path):
    """26 Nov: the 13:00 fire held the 5-min lock through the blast loop; no rows 13:05-close."""
    import fcntl
    import app.obs as obs
    import scripts.log_timeseries as lt

    monkeypatch.setattr(lt, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["log_timeseries.py", "--once", "--ignore-session"])
    monkeypatch.setattr(obs, "boot", lambda *a, **k: None)
    monkeypatch.setattr(obs, "shutdown", lambda *a, **k: None)
    data = SimpleNamespace(symbol="SENSEX")
    monkeypatch.setattr(lt, "capture_once", lambda min_gap=0, symbol=None: (f"[{symbol}] ok", data))
    monkeypatch.setattr(lt, "_note_capture", lambda *a, **k: None)
    monkeypatch.setattr(lt, "_in_blast_window", lambda ist: True)
    monkeypatch.setattr(lt, "_blast_already_running", lambda: False)
    monkeypatch.setattr(lt, "_capture_other_blasts", lambda sym: (True, set()))
    monkeypatch.setattr(lt, "_is_expiry_day", lambda d: True)

    lock_path = tmp_path / "data" / "journal" / ".log_timeseries_ALL.lock"
    seen: dict[str, bool] = {}

    def _blast_loop(symbol, not_today):
        with open(lock_path, "a+") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen["next_fire_can_lock"] = True
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                seen["next_fire_can_lock"] = False

    monkeypatch.setattr(lt, "_run_blast_window", _blast_loop)
    lt.main()
    assert seen == {"next_fire_can_lock": True}


def test_blast_capture_uses_the_published_book_before_a_live_pull(monkeypatch):
    """26 Nov: the cron's 1-min blast pulled Dhan 153 times beside ingest; all timed out."""
    import scripts.log_gamma_blast as lgb
    import scripts.log_timeseries as lt

    fetched = datetime(2026, 11, 26, 8, 0, tzinfo=timezone.utc)   # 13:30 IST
    book = SimpleNamespace(symbol="SENSEX", fetched_at=fetched, expiries=[date(2026, 11, 26)])
    asked: list[float] = []

    def _published(sym, max_age_s=180.0):
        asked.append(max_age_s)
        return book

    monkeypatch.setattr(lt, "published_book", _published)
    monkeypatch.setattr(lt, "_blast_already_running", lambda: True)
    monkeypatch.setattr(lgb, "load_dashboard", lambda **k: pytest.fail("live pull with a fresh book"))
    msg = lgb.capture_once(symbol="SENSEX")
    assert "blast left to the minute loop" in msg
    assert asked and asked[0] <= 90.0


def test_blast_capture_pulls_live_only_without_a_fresh_book(monkeypatch):
    import scripts.log_gamma_blast as lgb
    import scripts.log_timeseries as lt

    fetched = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)
    live = SimpleNamespace(symbol="SENSEX", fetched_at=fetched, expiries=[date(2026, 11, 27)])
    monkeypatch.setattr(lt, "published_book", lambda sym, max_age_s=180.0: None)
    pulls: list[str] = []
    monkeypatch.setattr(lgb, "load_dashboard", lambda symbol=None: pulls.append(symbol) or live)
    assert "not an expiry day" in lgb.capture_once(symbol="SENSEX")
    assert pulls == ["SENSEX"]


# ── Readiness tells the truth in session ──────────────────────────────────────────────────────

def _ok(**kw):
    return {"ok": True, "reason": "", **kw}


def _stub_probes(monkeypatch, degraded: set[str]):
    from app import readiness as rd
    monkeypatch.setattr(rd, "_chain_probe", lambda s: _ok(symbol=s, degraded=s in degraded,
                                                          has_snapshot=True, age_s=20.0))
    monkeypatch.setattr(rd, "_spot_probe", lambda s: _ok(symbol=s, age_s=1.0, spot=1.0))
    monkeypatch.setattr(rd, "_feed_probe", lambda: _ok())
    monkeypatch.setattr(rd, "_circuit_probe", lambda s: _ok(state="CLOSED"))
    monkeypatch.setattr(rd, "_ingest_probe", lambda: _ok(required=True))
    monkeypatch.setattr(rd, "_token_probe", lambda: _ok())
    monkeypatch.setattr(rd, "_nse_fallback_probe", lambda syms: {"active": False, "ok": True, "reason": ""})
    monkeypatch.setattr(config, "is_spot_ingest_open", lambda *a, **k: True)
    return rd


def test_readiness_is_ready_with_every_chain_live(monkeypatch):
    rd = _stub_probes(monkeypatch, degraded=set())
    payload = rd.build_readiness(market_open=True)
    assert payload["ready"] is True
    assert payload["degrade_mode"] == "OK"


def test_readiness_is_not_ready_while_any_chain_is_degraded(monkeypatch):
    """26 Nov: 216 of 375 session minutes CHAIN_DEGRADED while /api/health said ready."""
    rd = _stub_probes(monkeypatch, degraded={"SENSEX"})
    payload = rd.build_readiness(market_open=True)
    assert payload["ready"] is False
    assert "chain_degraded:SENSEX" in payload["reasons"]
    assert payload["degrade_mode"] == "CHAIN_DEGRADED"


def test_degraded_chain_off_session_does_not_fail_readiness(monkeypatch):
    rd = _stub_probes(monkeypatch, degraded={"NIFTY", "BANKNIFTY", "SENSEX"})
    assert rd.build_readiness(market_open=False)["ready"] is True


# ── Recommendation publisher survives a shared gamma strike ───────────────────────────────────

def test_recommendations_build_when_call_and_put_wall_share_a_strike():
    """25-26 Nov: call wall == put wall skipped the sigma_90 block; 412 publishes raised."""
    sys.path.insert(0, str(ROOT / "tests"))
    from test_trade_ideas import MockDashboardData
    import analytics.trade_ideas as ti
    from analytics.trade_ideas import generate_trade_recommendations

    ti.clear_latch_manager()
    try:
        for spot in (24500.0, 24480.0):
            data = MockDashboardData(spot=spot, symbol="NIFTY")
            data.levels.call_wall = 24500.0
            data.levels.put_wall = 24500.0
            batch = generate_trade_recommendations(data, min_score=0)
            assert batch.symbol == "NIFTY"
            assert batch.all_ce_candidates or batch.all_pe_candidates
    finally:
        ti.flush_telemetry(timeout=None)
        ti.clear_latch_manager()


# ── Narrator prompts carry datetimes ──────────────────────────────────────────────────────────

def test_every_prompt_builder_serialises_datetimes():
    """26 Nov: 107 narrator calls died on "Object of type datetime is not JSON serializable"."""
    from analytics import llm_prompts as lp
    now = datetime(2026, 11, 26, 13, 5, tzinfo=IST)
    facts = {"as_of": now, "expiry": date(2026, 11, 26), "strike": 84500}
    msgs = lp.commentary_story_messages(
        event_type="WALL_MIGRATION", event_facts=facts,
        template_headline="Call wall moved", template_body="84,000 to 84,500",
        session_context={"oi_ts": now, "spot": 84400.0},
    )
    assert json.loads(msgs[-1]["content"])["event_facts"]["expiry"] == "2026-11-26"
    for builder, arg in (
        (lp.alert_tape_messages, {"ts": now, "headline": "x"}),
        (lp.positioning_card_messages, {"as_of": now}),
        (lp.choppiness_card_messages, {"as_of": now}),
    ):
        out = builder(arg)
        json.loads(out[-1]["content"])


# ── Off-market snapshot does not call today's row stale ───────────────────────────────────────

def test_todays_row_before_the_close_is_not_a_stale_session(monkeypatch, tmp_path, caplog):
    """26 Nov: ~2,600 "snapshot 2026-11-26 is before the last session 2026-11-25" warnings."""
    from data import market_snapshot as ms
    row = {"symbol": "SENSEX", "spot": 84400.0, "as_of": "2026-11-26 09:12:00", "journal": str(tmp_path)}
    monkeypatch.setattr(ms, "_read_snapshot_row", lambda db, sym: dict(row))
    monkeypatch.setattr(ms, "candidate_journals", lambda: [])
    pre_open = datetime(2026, 11, 26, 9, 12, tzinfo=IST)
    with caplog.at_level("WARNING", logger=ms.log.name):
        got = ms.resolve_offmarket_snapshot("SENSEX", now=pre_open)
    assert got["stale_session"] is False
    assert "before the last session" not in caplog.text


def test_a_row_from_before_the_last_session_is_still_flagged(monkeypatch, tmp_path):
    from data import market_snapshot as ms
    row = {"symbol": "NIFTY", "spot": 25800.0, "as_of": "2026-11-24 15:29:00", "journal": str(tmp_path)}
    monkeypatch.setattr(ms, "_read_snapshot_row", lambda db, sym: dict(row))
    monkeypatch.setattr(ms, "candidate_journals", lambda: [])
    got = ms.resolve_offmarket_snapshot("NIFTY", now=datetime(2026, 11, 26, 9, 12, tzinfo=IST))
    assert got["stale_session"] is True


# ── Dhan packet time → true epoch ─────────────────────────────────────────────────────────────

def test_ltt_true_epoch_and_ist_shifted_both_land_on_the_same_instant():
    """25-26 Nov: always subtracting 5:30 stamped a 13:00-15:39 tape at 07:30-10:09."""
    from data.dhan_feed import ltt_to_epoch
    now = datetime(2026, 11, 26, 13, 0, tzinfo=IST).timestamp()
    true_epoch = int(now) - 3
    assert ltt_to_epoch(true_epoch, now=now) == true_epoch
    assert ltt_to_epoch(true_epoch + 19800, now=now) == true_epoch
    assert ltt_to_epoch(0, now=now) == int(now)
    assert ltt_to_epoch(None, now=now) == int(now)


def test_ltt_replay_of_a_past_day_keeps_the_ist_form():
    from data.dhan_feed import ltt_to_epoch
    now = datetime(2026, 11, 26, 20, 0, tzinfo=IST).timestamp()
    past = int(datetime(2026, 11, 19, 14, 0, tzinfo=IST).timestamp())
    assert ltt_to_epoch(past + 19800, now=now) == past


def test_tick_collectors_use_the_shared_ltt_helper():
    for rel in ("app/blast_ticks.py", "app/big_money.py", "app/big_money_live.py"):
        src = (ROOT / rel).read_text()
        assert "ltt_to_epoch(" in src, rel
        assert "int(tick.ltt) - _IST_OFFSET_S" not in src, rel


# ── GO auto-stage: no expiry-close lottery tickets ────────────────────────────────────────────

def test_auto_stage_skips_after_the_entry_cutoff_and_below_the_premium_floor():
    """26 Nov: auto-stage bought ₹0.1 Sensex calls at 15:30-15:32."""
    import web_server as ws
    fire = {"contracts": [{"action": "BUY", "opt_type": "CE", "ltp": 58.0}]}
    at = lambda h, m: datetime(2026, 11, 26, h, m, tzinfo=IST)  # noqa: E731
    assert ws._go_auto_stage_late_or_cheap(fire, now=at(15, 1)) == ""
    assert ws._go_auto_stage_late_or_cheap(fire, now=at(15, 30)) == "after_entry_cutoff"
    cheap = {"contracts": [{"action": "BUY", "opt_type": "CE", "ltp": 0.1}]}
    assert ws._go_auto_stage_late_or_cheap(cheap, now=at(14, 0)) == "premium_below_floor"
