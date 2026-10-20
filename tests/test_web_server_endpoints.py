"""tests/test_web_server_endpoints.py — Comprehensive Integration Tests for FastAPI Web Cockpit.

Validates all 16+ REST and HTML endpoints, payload schemas, 1-click execution,
IST timestamp alignment (+19,800s), and multi-profile popout chart parity.
"""

import asyncio
import json
import re
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from web_server import app, _to_ist_chart_timestamp
import config
import data.journal.store as journal_store


async def asgi_request(method: str, path: str, body: dict = None, headers: dict = None):
    """Zero-dependency in-memory ASGI caller for FastAPI application testing."""
    clean_path = path.split("?")[0]
    query_str = path.split("?", 1)[1].encode() if "?" in path else b""
    body_bytes = json.dumps(body).encode() if body is not None else b""
    req_headers = {k.lower(): v for k, v in (headers or {}).items()}
    if body is not None and "content-type" not in req_headers:
        req_headers["content-type"] = "application/json"

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "path": clean_path,
        "raw_path": clean_path.encode(),
        "query_string": query_str,
        "headers": [[k.encode(), v.encode()] for k, v in req_headers.items()],
    }

    receive_state = {"done": False}

    async def receive():
        if not receive_state["done"]:
            receive_state["done"] = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        return {"type": "http.disconnect"}

    response_headers = {}
    response_body = []
    response_status = [None]

    async def send(message):
        if message["type"] == "http.response.start":
            response_status[0] = message["status"]
            for k, v in message.get("headers", []):
                response_headers[k.decode()] = v.decode()
        elif message["type"] == "http.response.body":
            response_body.append(message.get("body", b""))

    await app(scope, receive, send)
    content = b"".join(response_body)
    return response_status[0], response_headers, content


@pytest.fixture(autouse=True)
def fresh_journal_db(tmp_path, monkeypatch):
    """Isolate journal tests to a fresh temporary database."""
    tmp_db_path = tmp_path / "test_web_journal.db"
    monkeypatch.setattr(journal_store, "_DB_PATH", tmp_db_path)
    journal_store.ensure_schema()
    yield tmp_db_path


def test_get_root_cockpit_html():
    """Verify GET / renders main single-page cockpit app with all 11 tab containers."""
    status, headers, content = asyncio.run(asgi_request("GET", "/"))
    assert status == 200
    assert "text/html" in headers.get("content-type", "")
    html = content.decode("utf-8")
    assert "Cubera GEX Trading Cockpit" in html
    assert 'id="tab-cockpit"' in html
    assert 'id="tab-target-engine"' in html
    assert 'id="tab-recommendations"' in html
    assert 'id="tab-sentinel"' in html
    assert 'id="tab-efficiency"' in html
    assert 'id="tab-market-intel"' in html
    assert 'id="tab-analytics"' in html
    assert 'id="tab-journal"' in html
    assert 'id="tab-scorecard"' in html
    assert 'id="tab-ops"' in html
    assert 'id="tab-commentary"' in html


def test_get_status_schema_and_ist_clock():
    """Verify GET /api/status returns valid state and clean IST clock."""
    status, headers, content = asyncio.run(asgi_request("GET", "/api/status?symbol=NIFTY&window=current_week"))
    assert status == 200
    data = json.loads(content)
    assert data["symbol"] == "NIFTY"
    assert "ist_time" in data
    # Enforce standard "%H:%M:%S IST" time format
    assert data["ist_time"].endswith(" IST")
    assert len(data["ist_time"].split()[0].split(":")) == 3
    assert "spot" in data
    assert "is_market_open" in data
    assert "session_phase" in data


def test_get_indices():
    """Verify GET /api/indices returns benchmark indices list."""
    status, headers, content = asyncio.run(asgi_request("GET", "/api/indices"))
    assert status == 200
    data = json.loads(content)
    assert isinstance(data, list)
    symbols = [idx["symbol"] for idx in data]
    assert "NIFTY" in symbols
    assert "BANKNIFTY" in symbols


def test_get_cockpit_data_levels_and_tape():
    """Verify GET /api/cockpit-data returns GEX levels, spot, and tape alerts."""
    status, headers, content = asyncio.run(asgi_request("GET", "/api/cockpit-data?symbol=NIFTY&window=current_week"))
    assert status == 200
    data = json.loads(content)
    assert "spot" in data
    assert "kpis" in data
    assert "call_wall" in data["kpis"]
    assert "put_wall" in data["kpis"]
    assert "gamma_flip" in data["kpis"]
    assert data["kpis"]["gamma_flip"]["val"] > 0
    assert "max_pain" in data["kpis"]
    assert "directional_bias" in data["kpis"]
    assert "gamma_blast" in data
    assert "ladder" in data
    assert len(data["ladder"]) == 11
    assert "is_max_pain" in data["ladder"][0]
    assert "is_magnet" in data["ladder"][0]
    assert "is_gamma_flip" in data["ladder"][0]
    assert any(r["is_gamma_flip"] for r in data["ladder"])
    assert "orderflow_alerts" in data
    assert "pre_market" in data
    assert "cockpit_recommendations" in data
    assert "pinned_count" in data
    assert "fresh_count" in data
    assert "total_cockpit_cards" in data
    assert data["total_cockpit_cards"] == data["pinned_count"] + data["fresh_count"]


def test_cockpit_recommendations_pinned_and_fresh_setups():
    """Verify Cockpit recommendations include dual-pipeline pinned and fresh setups."""
    status, headers, content = asyncio.run(asgi_request("GET", "/api/cockpit-data?symbol=NIFTY&window=current_week"))
    assert status == 200
    data = json.loads(content)
    assert isinstance(data["cockpit_recommendations"], list)
    assert isinstance(data["pinned_count"], int)
    assert isinstance(data["fresh_count"], int)
    assert data["pinned_count"] >= 0
    assert data["fresh_count"] >= 0
    for card in data["cockpit_recommendations"]:
        assert "rec_id" in card
        assert "side" in card
        assert "strike" in card
        assert "quality_score" in card
        assert "is_pinned" in card
        assert "is_fresh" in card


def test_target_engine_candidate_structures_table():
    """Verify GET /api/target-engine returns confluence score and candidate structures."""
    status, headers, content = asyncio.run(asgi_request("GET", "/api/target-engine?symbol=NIFTY&window=current_week"))
    assert status == 200
    data = json.loads(content)
    assert "dial" in data
    assert "targets" in data
    assert "candidates" in data
    # Candidate structures must provide actionable option trade setups
    assert isinstance(data["candidates"], list)
    if data["candidates"]:
        first = data["candidates"][0]
        assert "strike" in first
        assert "target_1" in first
        assert "stop_loss" in first


def test_target_engine_reprice_calculator():
    """Verify POST /api/target-engine/reprice runs Black-76 Greek repricing."""
    payload = {
        "symbol": "NIFTY",
        "spot": 24500.0,
        "dte": 3.0,
        "vol_shift": 0.02,
        "time_decay_pct": 0.25,
        "scenario": "bull",
        "rate": 0.07,
    }
    status, headers, content = asyncio.run(asgi_request("POST", "/api/target-engine/reprice", body=payload))
    assert status == 200
    data = json.loads(content)
    assert "contract" in data
    assert "base_fut" in data
    assert "greeks" in data
    assert "p_theo_curr" in data


def test_recommendations_and_1click_execution():
    """Verify GET /api/recommendations and POST /api/recommendations/execute."""
    status, headers, content = asyncio.run(
        asgi_request("GET", "/api/recommendations?symbol=NIFTY&window=current_week&capital=500000&risk=1.5")
    )
    assert status == 200
    rec_data = json.loads(content)
    assert "higher_conviction" in rec_data or "trade_setups" in rec_data

    # Execute 1-click paper trade execution
    exec_payload = {
        "rec_id": "REC_NIFTY_TEST_SPREAD",
        "symbol": "NIFTY",
        "strike": 24500.0,
        "side": "CE",
        "lots": 2,
        "ltp": 85.5,
        "strategy_name": "⚡ Bull Call Spread",
        "spot": 24500.0,
        "regime": "MOMENTUM_BULL",
    }
    exec_status, _, exec_content = asyncio.run(
        asgi_request("POST", "/api/recommendations/execute", body=exec_payload)
    )
    assert exec_status == 200
    exec_res = json.loads(exec_content)
    assert exec_res["status"] == "success"
    assert "trade_id" in exec_res

    # Assert persisted in SQLite journal store
    open_trades = journal_store.list_open_trades(symbol="NIFTY")
    assert len(open_trades) == 1
    trade = open_trades[0]
    assert trade.trade_id == exec_res["trade_id"]
    assert trade.strategy_name == "⚡ Bull Call Spread"


def test_sentinel_tape_ist_timestamps_and_series():
    """Verify GET /api/sentinel-tape returns bars with IST-shifted timestamps and EMA series."""
    status, headers, content = asyncio.run(asgi_request("GET", "/api/sentinel-tape?symbol=NIFTY&timeframe=1m"))
    assert status == 200
    data = json.loads(content)
    assert "bars" in data
    assert "candles" in data
    assert "cvd" in data
    assert "ema9" in data
    assert "ema21" in data
    assert "daily_suite" in data
    assert "block_tape" in data

    if data["bars"]:
        # Verify timestamps are shifted by +19,800s so UTC formatter outputs IST market hours (09:15 - 15:30)
        first_bar = data["bars"][0]
        last_bar = data["bars"][-1]
        dt_last = datetime.fromtimestamp(last_bar["time"], tz=timezone.utc)
        # Session close at 15:30 IST renders as 15:30:00 UTC
        assert dt_last.hour in range(9, 16)


def test_options_analytics_kpi_and_smile():
    """Verify GET /api/options-analytics returns 6 KPI cards and IV smile curve."""
    status, headers, content = asyncio.run(asgi_request("GET", "/api/options-analytics?symbol=NIFTY"))
    assert status == 200
    data = json.loads(content)
    assert "kpis" in data
    assert "iv_skew_curve" in data
    assert "buildup_matrix" in data


def test_journal_and_scorecard():
    """Verify GET /api/journal and GET /api/scorecard endpoints."""
    j_status, _, j_content = asyncio.run(asgi_request("GET", "/api/journal"))
    assert j_status == 200
    j_data = json.loads(j_content)
    assert "open_positions" in j_data
    assert "risk_hud" in j_data

    s_status, _, s_content = asyncio.run(asgi_request("GET", "/api/scorecard"))
    assert s_status == 200
    s_data = json.loads(s_content)
    assert "kpis" in s_data
    assert "equity_curve" in s_data


def test_ops_calendar_timeline_windows():
    """Verify GET /api/ops-calendar returns the 11-window Indian market timeline."""
    status, headers, content = asyncio.run(asgi_request("GET", "/api/ops-calendar"))
    assert status == 200
    data = json.loads(content)
    assert "timeline_windows" in data
    assert len(data["timeline_windows"]) == 11
    assert "resilience" in data


def test_standalone_popout_chart_all_profiles():
    """Verify GET /chart/sentinel renders all 4 profiles (Full, Pure, CVD, Trend)."""
    for prof in ["Full", "Pure", "CVD", "Trend"]:
        status, headers, content = asyncio.run(
            asgi_request("GET", f"/chart/sentinel?symbol=NIFTY&timeframe=1m&profile={prof}")
        )
        assert status == 200
        assert "text/html" in headers.get("content-type", "")
        html = content.decode("utf-8")
        assert f'data-profile="{prof}"' in html
        assert "switchProfile(" in html
        assert "candleSeries" in html
        assert "vwapSeries" in html
        assert "ema9Series" in html
        assert "cvdSeries" in html
        assert "popout-time" in html


# ==============================================================================
# RULE OF SINGLE TIMEZONE AUTHORITY (IST ONLY)
# ==============================================================================
class TestSingleTimezoneAuthority:
    """Verifies all timestamps across endpoints and charts conform strictly to IST (UTC+05:30)."""

    def test_to_ist_chart_timestamp_conversion(self):
        """Verify _to_ist_chart_timestamp applies the +19,800s shift to match IST calendar hours."""
        # 1. Aware datetime at 15:30 IST (+05:30)
        dt_close_ist = datetime(2026, 10, 16, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        ts_close = _to_ist_chart_timestamp(dt_close_ist)
        # When formatted using UTC (Lightweight Charts' internal renderer), it displays 15:30:00
        readable_close = datetime.fromtimestamp(ts_close, tz=timezone.utc).strftime("%H:%M:%S")
        assert readable_close == "15:30:00"

        # 2. Aware datetime at 09:15 IST (+05:30)
        dt_open_ist = datetime(2026, 10, 16, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        ts_open = _to_ist_chart_timestamp(dt_open_ist)
        readable_open = datetime.fromtimestamp(ts_open, tz=timezone.utc).strftime("%H:%M:%S")
        assert readable_open == "09:15:00"

        # 3. Naive datetime at 09:15
        dt_naive = datetime(2026, 10, 16, 9, 15)
        ts_naive = _to_ist_chart_timestamp(dt_naive)
        readable_naive = datetime.fromtimestamp(ts_naive, tz=timezone.utc).strftime("%H:%M:%S")
        assert readable_naive == "09:15:00"

        # 4. ISO string with +05:30
        ts_str = _to_ist_chart_timestamp("2026-10-16T15:30:00+05:30")
        readable_str = datetime.fromtimestamp(ts_str, tz=timezone.utc).strftime("%H:%M:%S")
        assert readable_str == "15:30:00"

    def test_api_status_ist_clock_format(self):
        """Verify GET /api/status ist_time strictly matches 'HH:MM:SS IST' with no double-IST or ISO tokens."""
        status, _, content = asyncio.run(asgi_request("GET", "/api/status?symbol=NIFTY"))
        assert status == 200
        data = json.loads(content)
        ist_time = data.get("ist_time", "")
        # Strict pattern: two digits : two digits : two digits space IST
        assert re.match(r"^\d{2}:\d{2}:\d{2} IST$", ist_time), f"Invalid clock format: {ist_time}"
        assert not ist_time.endswith(" IST IST")
        assert "-" not in ist_time  # Ensure no date component like YYYY-MM-DD
        assert "+" not in ist_time  # Ensure no raw +05:30 offset component

    def test_sentinel_signals_and_tape_ist_timestamps(self):
        """Verify Sentinel signals, block tape, and candlestick bars conform to IST formatting."""
        status, _, content = asyncio.run(asgi_request("GET", "/api/sentinel-tape?symbol=NIFTY&timeframe=1m"))
        assert status == 200
        data = json.loads(content)

        # Check signals: must end with ' IST' and not contain double IST
        signals = data.get("signals", [])
        for s in signals[:5]:
            t_str = s.get("time", "")
            assert t_str.endswith(" IST"), f"Signal time missing IST: {t_str}"
            assert not t_str.endswith(" IST IST"), f"Signal time double IST: {t_str}"

        # Check block tape: must be 'HH:MM:SS' format
        tape = data.get("block_tape", [])
        for t in tape[:5]:
            t_str = t.get("time", "")
            assert re.match(r"^\d{2}:\d{2}:\d{2}$", t_str), f"Invalid tape time: {t_str}"

        # Check candlestick bars: all bars must fall within Indian market hours (09:15 - 15:30)
        bars = data.get("bars", [])
        for b in bars:
            dt_bar = datetime.fromtimestamp(b["time"], tz=timezone.utc)
            assert dt_bar.hour in range(9, 16), f"Bar outside market hours: {dt_bar}"


# ==============================================================================
# RULE OF ZERO SYNTHETIC NOISE OUTSIDE SIMULATOR
# ==============================================================================
class TestZeroSyntheticNoiseOutsideSimulator:
    """Verifies that no mock/demo fluctuations or synthetic seeds execute outside Simulator Mode."""

    def test_reseed_endpoint_gated_outside_simulator(self):
        """Verify POST /api/sentinel-tape/reseed rejects execution when simulation_mode is False or omitted."""
        # 1. simulation_mode = False explicitly
        status, _, content = asyncio.run(
            asgi_request("POST", "/api/sentinel-tape/reseed", body={"symbol": "NIFTY", "simulation_mode": False})
        )
        assert status == 200
        data = json.loads(content)
        assert data.get("status") == "ignored"
        assert "disabled outside of Simulator Mode" in data.get("message", "")

        # 2. simulation_mode omitted
        status, _, content = asyncio.run(
            asgi_request("POST", "/api/sentinel-tape/reseed", body={"symbol": "NIFTY"})
        )
        assert status == 200
        data = json.loads(content)
        assert data.get("status") == "ignored"

    def test_spot_quote_stability_zero_decimal_flicker(self):
        """Verify spot prices across consecutive calls are 100% deterministic with zero artificial jitter."""
        # Make 5 rapid consecutive calls to /api/status
        status_spots = []
        for _ in range(5):
            status, _, content = asyncio.run(asgi_request("GET", "/api/status?symbol=NIFTY"))
            assert status == 200
            status_spots.append(json.loads(content)["spot"])

        # All 5 spot prices must be identical with zero decimal noise or random drift
        assert len(set(status_spots)) == 1, f"Spot jitter detected in status: {status_spots}"

        # Make 5 rapid consecutive calls to /api/cockpit-data
        cockpit_spots = []
        for _ in range(5):
            status, _, content = asyncio.run(asgi_request("GET", "/api/cockpit-data?symbol=NIFTY"))
            assert status == 200
            cockpit_spots.append(json.loads(content)["spot"])

        assert len(set(cockpit_spots)) == 1, f"Spot jitter detected in cockpit-data: {cockpit_spots}"
        assert status_spots[0] == cockpit_spots[0], "Spot price mismatch between status and cockpit-data"


# ==============================================================================
# RULE OF OFF-MARKET PERSISTENCE
# ==============================================================================
class TestOffMarketPersistence:
    """Verifies all endpoints gracefully load latest database snapshot when market is closed."""

    def test_off_market_status_and_cockpit_data_persistence(self):
        """Verify /api/status and /api/cockpit-data return fully populated data when is_market_open is False."""
        with patch.object(config, "is_market_open", return_value=False):
            # 1. /api/status
            s_status, _, s_content = asyncio.run(asgi_request("GET", "/api/status?symbol=NIFTY"))
            assert s_status == 200
            s_data = json.loads(s_content)
            assert s_data["spot"] is not None and s_data["spot"] > 0
            assert s_data["lot_size"] > 0
            assert s_data["day_ohlc"]["close"] > 0
            assert "ist_time" in s_data

            # 2. /api/cockpit-data
            c_status, _, c_content = asyncio.run(asgi_request("GET", "/api/cockpit-data?symbol=NIFTY"))
            assert c_status == 200
            c_data = json.loads(c_content)
            assert c_data["spot"] is not None and c_data["spot"] > 0
            assert "kpis" in c_data
            assert "gamma_blast" in c_data
            assert "ladder" in c_data
            assert len(c_data["ladder"]) > 0
            assert c_data["fresh_count"] == 0
            assert len([c for c in c_data.get("cockpit_recommendations", []) if c.get("is_fresh")]) == 0

    def test_off_market_recommendations_and_target_engine_persistence(self):
        """Verify recommendations and target engine do not fail or return blank states when market is closed."""
        with patch.object(config, "is_market_open", return_value=False):
            # 1. /api/target-engine
            te_status, _, te_content = asyncio.run(asgi_request("GET", "/api/target-engine?symbol=NIFTY&horizon=120&mode=heuristic"))
            assert te_status == 200
            te_data = json.loads(te_content)
            assert "dial" in te_data
            assert "targets" in te_data
            assert "scenario_set" in te_data
            assert "dominant_scenario" in te_data["scenario_set"]
            assert "probabilities" in te_data["scenario_set"]
            assert "bull" in te_data["targets"]
            assert "p_touch_target" in te_data["targets"]["bull"]
            assert "p_close_beyond" in te_data["targets"]["bull"]
            assert "forecast_iv" in te_data["targets"]["bull"]
            assert "candidates" in te_data
            assert len(te_data["candidates"]) > 0

            # 2. /api/recommendations
            rec_status, _, rec_content = asyncio.run(
                asgi_request("GET", "/api/recommendations?symbol=NIFTY&window=current_week&capital=500000&risk=1.5")
            )
            assert rec_status == 200
            rec_data = json.loads(rec_content)
            # Must return graceful off-market guidance or cached setups, never HTTP error
            assert "higher_conviction" in rec_data or "closed_notice" in rec_data

    def test_off_market_sentinel_tape_and_commentary_persistence(self):
        """Verify Sentinel tape and session commentary retain completed session records when market is closed."""
        with patch.object(config, "is_market_open", return_value=False):
            # 1. /api/sentinel-tape
            st_status, _, st_content = asyncio.run(asgi_request("GET", "/api/sentinel-tape?symbol=NIFTY"))
            assert st_status == 200
            st_data = json.loads(st_content)
            assert "bars" in st_data
            assert len(st_data["bars"]) > 0
            assert "daily_suite" in st_data
            assert len(st_data["daily_suite"]) > 0

            # 2. /api/session-commentary
            sc_status, _, sc_content = asyncio.run(asgi_request("GET", "/api/session-commentary?symbol=NIFTY"))
            assert sc_status == 200
            sc_data = json.loads(sc_content)
            assert "cards" in sc_data
            assert "session_date" in sc_data

    def test_health_check_endpoint(self):
        """Verify /api/health endpoint returns 200, valid status, and semantic version."""
        status, _, content = asyncio.run(asgi_request("GET", "/api/health"))
        assert status == 200
        data = json.loads(content)
        assert data["status"] == "ok"
        assert data["app"] == "Cubera GEX Cockpit"
        assert "version" in data
        assert "env" in data
        assert "timestamp" in data
        assert "market_open" in data

    def test_documentation_endpoints(self):
        """Verify documentation REST API and rendered HTML endpoints for all 12 guides."""
        from web_server import DOCS_MAP

        # 1. /api/docs/list
        status, _, content = asyncio.run(asgi_request("GET", "/api/docs/list"))
        assert status == 200
        data = json.loads(content)
        assert "guides" in data
        assert len(data["guides"]) == 12

        # 2. /api/docs/{guide_id}
        status, _, content = asyncio.run(asgi_request("GET", "/api/docs/user_guide"))
        assert status == 200
        doc = json.loads(content)
        assert doc["id"] == "user_guide"
        assert "Master User Guide" in doc["title"]
        assert len(doc["content"]) > 100

        # 3. /guide/{guide_id} HTML rendering
        status, headers, content = asyncio.run(asgi_request("GET", "/guide/user_guide"))
        assert status == 200
        assert "text/html" in headers.get("content-type", "")
        html = content.decode("utf-8")
        assert "marked.min.js" in html
        assert "Quick Guide Jump:" in html
        assert "Master User Guide" in html

        # 4. /manuals/{guide_id} alias
        status, _, content = asyncio.run(asgi_request("GET", "/manuals/orderflow_guide"))
        assert status == 200
        html = content.decode("utf-8")
        assert "OrderFlow" in html

        # 5. /?view={guide_id} query parameter
        status, _, content = asyncio.run(asgi_request("GET", "/?view=target_engine_guide"))
        assert status == 200
        html = content.decode("utf-8")
        assert "Target Engine" in html

    def test_all_docs_files_exist_on_disk(self):
        """Verify all 12 markdown source manuals mapped in DOCS_MAP exist on disk and have content."""
        from web_server import DOCS_MAP, ROOT_DIR

        assert len(DOCS_MAP) == 12
        for doc_id, info in DOCS_MAP.items():
            doc_file = ROOT_DIR / info["file"]
            assert doc_file.exists(), f"Missing docs markdown file: {info['file']} for {doc_id}"
            assert doc_file.stat().st_size > 200, f"Docs file too small or empty: {info['file']}"


def test_commentary_log_market_alert():
    """Verify POST /api/commentary/log-market-alert logs an alert into session commentary with 30m TTL."""
    payload = {
        "symbol": "NIFTY",
        "scenario": "VOLATILITY_EXPANSION",
        "badge": "⚠️ BIG MOVE COMING",
        "headline": "VOLATILITY EXPANSION IMMINENT",
        "body": "Market entering fast accelerator zone.",
        "action_advice": "Buy breakout momentum with tight stops.",
        "diagnostics": "Spot: 24,480 | Flip: 24,545"
    }
    try:
        status, headers, content = asyncio.run(
            asgi_request("POST", "/api/commentary/log-market-alert", body=payload)
        )
        assert status == 200
        data = json.loads(content)
        assert data["status"] == "ok"
        assert "entry_id" in data
        assert data["ttl"] == "30m"
        assert "logged_at" in data
    finally:
        try:
            if "data" in locals() and "entry_id" in data:
                from commentary.storage.db import _get_write_conn
                with _get_write_conn() as conn:
                    conn.execute("DELETE FROM commentary_entries WHERE entry_id = ?", (data["entry_id"],))
                    conn.commit()
        except Exception:
            pass


def test_cockpit_tradingview_chart_in_index_html():
    """Verify that index.html contains the Cockpit Chart card, timeframe controls, and GEX Canvas."""
    status, headers, content = asyncio.run(asgi_request("GET", "/"))
    assert status == 200
    html = content.decode("utf-8")
    assert 'id="cockpit-tv-card"' in html
    assert 'id="btn-cockpit-tf-1m"' in html
    assert 'id="btn-cockpit-tf-5m"' in html
    assert 'id="btn-cockpit-tf-15m"' in html
    assert 'id="cockpit-chart-engine-badge"' in html
    assert 'id="cockpit-chart-viewport"' in html
    assert 'id="cockpit-gex-canvas-container"' in html


def test_standalone_tradingview_popout_endpoint():
    """Verify GET /chart/tradingview returns standalone HTML popout with 1m, 5min, 15m timeframe options."""
    # Test NIFTY 5m
    status, headers, content = asyncio.run(asgi_request("GET", "/chart/tradingview?symbol=NIFTY&timeframe=5m"))
    assert status == 200
    assert "text/html" in headers.get("content-type", "")
    html = content.decode("utf-8")
    assert "NIFTY GEX Index Chart" in html
    assert "switchTf('1m')" in html
    assert "switchTf('5m')" in html
    assert "switchTf('15m')" in html
    assert "gex-mount" in html

    # Test BANKNIFTY 1m
    status, _, content = asyncio.run(asgi_request("GET", "/chart/tradingview?symbol=BANKNIFTY&timeframe=1m"))
    assert status == 200
    assert "BANKNIFTY GEX Index Chart" in content.decode("utf-8")

    # Test SENSEX 15m
    status, _, content = asyncio.run(asgi_request("GET", "/chart/tradingview?symbol=SENSEX&timeframe=15m"))
    assert status == 200
    assert "SENSEX GEX Index Chart" in content.decode("utf-8")
    assert "btn-popout-theme" in content.decode("utf-8")
    assert "theme-light" in content.decode("utf-8")


def test_dark_light_theme_mode_toggle():
    """Verify that index.html contains theme toggle buttons, early FOUC prevention, and static assets define light mode."""
    # 1. Check index.html
    status, headers, content = asyncio.run(asgi_request("GET", "/"))
    assert status == 200
    html = content.decode("utf-8")
    assert 'id="btn-theme-toggle"' in html, "Header theme toggle button missing"
    assert 'id="btn-sidebar-theme-toggle"' in html, "Sidebar theme toggle button missing"
    assert "cubera_theme" in html, "Early theme initializer missing in <head>"
    assert 'data-theme' in html, "data-theme attribute logic missing"

    # 2. Check cockpit.css has light mode tokens
    status, _, content = asyncio.run(asgi_request("GET", "/static/css/cockpit.css"))
    assert status == 200
    css = content.decode("utf-8")
    assert '[data-theme="light"]' in css, "Light theme CSS rules missing in cockpit.css"
    assert "theme-light" in css, "theme-light class missing in cockpit.css"
    assert "--bg: #f1f5f9" in css, "Light theme background variable missing"

    # 3. Check cockpit.js has theme functions
    status, _, content = asyncio.run(asgi_request("GET", "/static/js/cockpit.js"))
    assert status == 200
    js = content.decode("utf-8")
    assert "function applyTheme" in js, "applyTheme function missing in cockpit.js"
    assert "function toggleTheme" in js, "toggleTheme function missing in cockpit.js"
    assert "btn-theme-toggle" in js, "Theme toggle event binding missing in cockpit.js"
    assert "cubera_theme" in js, "Theme localStorage persistence missing in cockpit.js"





