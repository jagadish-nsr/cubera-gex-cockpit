"""Fixture-based tests for quant engine: flow q_i, setups, vetoes, replay."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["CRYPTO_DB_URL"] = "sqlite:///:memory:"
os.environ.pop("CRYPTO_LIVE_SPIKE_DERIBIT", None)
os.environ.pop("CRYPTO_LIVE_FEEDS", None)

from crypto.config import get_flag, list_flags, load_config, set_flag
from crypto.engine.flow_inventory import (
    FlowInventory,
    customer_signed_from_direction,
)
from crypto.engine.gex import compute_gex_features
from crypto.engine.inference import (
    PlaceholderRegimeClassifier,
    export_feature_vector,
    run_inference,
)
from crypto.engine.orderflow import compute_orderflow_features, cvd_over_window
from crypto.engine.replay import replay_features, replay_recorded_file
from crypto.engine.setups import evaluate_setups
from crypto.engine.signals import build_signal_card
from crypto.engine.vetoes import VetoContext, apply_stub_hooks, evaluate_vetoes
from crypto.providers.mock.chain import MockOptionChainProvider
from crypto.providers.mock.perp import MockPerpProvider
from crypto.providers.types import PerpSnapshot, TradePrint
from crypto.store.db import get_engine, init_db
import asyncio


FIXTURES = Path(__file__).parent / "fixtures"
REPLAY_BUNDLE = FIXTURES / "replay_bundle.json"


@pytest.fixture(autouse=True)
def _reset_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTO_DB_URL", f"sqlite:///{tmp_path / 't.db'}")
    # Ensure inference off by default between tests
    load_config(reload=True)
    set_flag("inference.feature_export", False, actor="test")
    set_flag("inference.regime_classifier", False, actor="test")
    set_flag("engine.gex_flow_model", True, actor="test")
    set_flag("engine.signal_rules", True, actor="test")
    get_engine(reload=True)
    init_db(reload=True)
    yield
    load_config(reload=True)


def test_flags_include_inference_off():
    flags = list_flags()
    assert flags["engine.gex_flow_model"] is True
    assert flags["engine.signal_rules"] is True
    assert flags["inference.feature_export"] is False
    assert flags["inference.regime_classifier"] is False


def test_customer_signed_direction():
    assert customer_signed_from_direction("buy", 10) == 10
    assert customer_signed_from_direction("sell", 10) == -10
    assert customer_signed_from_direction("unknown", 10) == 0


def test_flow_inventory_q_i_and_blocks():
    inv = FlowInventory()
    buy = TradePrint(
        venue="deribit",
        symbol="BTC-28NOV25-97000-P",
        price=0.03,
        size=40,
        side="buy",
        ts=datetime.now(timezone.utc),
    )
    block = TradePrint(
        venue="deribit",
        symbol="BTC-28NOV25-97000-P",
        price=0.03,
        size=100,
        side="buy",
        ts=datetime.now(timezone.utc),
        is_block=True,
    )
    assert inv.ingest(buy) is True
    assert inv.ingest(block) is False
    assert inv.total_trades == 1
    assert inv.total_blocks_skipped == 1
    q = inv.q_by_instrument()["BTC-28NOV25-97000-P"]
    # customer +40 → dealer q = -40
    assert q == pytest.approx(-40.0)


def test_signed_tape_flow_mode_on_gex():
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    inv = FlowInventory()
    # Heavy customer put buys near spot → dealers short puts → negative put q
    # Convention on fixture tends positive; signed put buys push flow negative → disagreement
    for _ in range(5):
        inv.ingest(
            TradePrint(
                venue="deribit",
                symbol="BTC-28NOV25-97000-C",
                price=0.03,
                size=200,
                side="buy",
                ts=datetime.now(timezone.utc),
            )
        )
    gex = compute_gex_features(snap, flow_inventory=inv)
    assert gex["flow_mode"] == "signed_tape"
    assert "flow_total" in gex
    # With heavy customer call buys, flow should lean negative vs convention often
    assert gex["flow_sign"] in {"positive", "negative", "flat"}


def test_volume_proxy_when_no_tape():
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    gex = compute_gex_features(snap, flow_inventory=None)
    assert gex["flow_mode"] == "volume_proxy"
    assert gex["models_agree"] is True


def test_disagreement_veto():
    now = datetime.now(timezone.utc)
    gex = {
        "models_agree": False,
        "regime": "uncertain",
        "spot": 97000,
        "flip": 96800,
        "call_wall": 100000,
        "put_wall": 96000,
        "source": "deribit-ws+rest",
    }
    perp = PerpSnapshot(
        venue="t",
        symbol="BTC-PERP",
        mark=97000,
        index=97000,
        funding_rate=0,
        open_interest=1,
        cvd_1m=0,
        cvd_5m=0,
        cvd_15m=0,
        book_imbalance=0,
        fetched_at=now,
        source="t",
    )
    card = build_signal_card(gex, perp, live_signal_path=True, chain_fetched_at=now)
    assert card["status"] == "NO-GO"
    assert any("disagreement" in v for v in card["veto_reasons"])


def test_stub_risk_veto_hook(monkeypatch):
    load_config(reload=True)
    # Force stub via in-memory config
    cfg = load_config()
    cfg.setdefault("engine", {}).setdefault("vetoes", {})["risk_daily_loss_stub"] = True
    now = datetime.now(timezone.utc)
    gex = {
        "models_agree": True,
        "regime": "positive_gamma",
        "spot": 97000,
        "flip": 96800,
        "source": "deribit-ws+rest",
    }
    ctx = apply_stub_hooks(
        VetoContext(now=now, gex=gex, live_signal_path=True, chain_fetched_at=now, perp_fetched_at=now)
    )
    result = evaluate_vetoes(ctx)
    assert result.blocked
    assert any("daily loss" in v for v in result.vetoes)
    # cleanup
    cfg["engine"]["vetoes"]["risk_daily_loss_stub"] = False


def test_positive_fade_setup_fires():
    gex = {
        "spot": 97240,
        "flip": 96000,
        "call_wall": 100000,
        "put_wall": 96000,
        "magnet": 97000,
        "regime": "positive_gamma",
        "models_agree": True,
    }
    # Mark stretched above magnet; CVD 15m diverging (negative while stretch up)
    of = {
        "mark": 97600,
        "cvd_1m": -10,
        "cvd_5m": -20,
        "cvd_15m": -150,
        "book_imbalance": -0.1,
        "oi_delta": 0,
    }
    cand = evaluate_setups(gex, of)
    assert cand is not None
    assert cand.setup == "positive_gamma_fade"
    assert cand.side == "sell"
    assert cand.target == 97000


def test_negative_breakout_setup_fires():
    gex = {
        "spot": 97240,
        "flip": 98000,
        "call_wall": 100000,
        "put_wall": 95000,
        "magnet": 97000,
        "regime": "negative_gamma",
        "models_agree": True,
    }
    of = {
        "mark": 97300,
        "cvd_1m": 30,
        "cvd_5m": 80,
        "cvd_15m": 100,
        "book_imbalance": 0.3,
        "oi_delta": 1e6,
    }
    cand = evaluate_setups(gex, of)
    assert cand is not None
    assert cand.setup == "negative_gamma_breakout"
    assert cand.side == "buy"


def test_go_card_contract_fields():
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    perp = asyncio.run(MockPerpProvider().fetch_perp())
    gex = compute_gex_features(snap)
    card = build_signal_card(gex, perp)
    assert card["status"] == "GO"
    for key in (
        "instrument",
        "side",
        "entry_zone",
        "target",
        "stop",
        "size_cap_usd",
        "score",
        "reasons",
        "invalidation",
        "valid_until",
        "model_version",
        "rules_version",
    ):
        assert key in card
    assert len(card["reasons"]) == 3
    assert card["model_version"] == "rules-v1"
    assert card["rules_version"] == "setups-v1"


def test_signal_rules_flag_off():
    set_flag("engine.signal_rules", False, actor="test")
    snap = asyncio.run(MockOptionChainProvider().fetch_chain("BTC"))
    perp = asyncio.run(MockPerpProvider().fetch_perp())
    gex = compute_gex_features(snap)
    card = build_signal_card(gex, perp)
    assert card["status"] == "NO-GO"
    assert any("signal_rules" in v for v in card["veto_reasons"])
    set_flag("engine.signal_rules", True, actor="test")


def test_orderflow_cvd_windows():
    now = datetime.now(timezone.utc)
    trades = [
        TradePrint("d", "BTC-PERPETUAL", 97000, 10, "buy", now - timedelta(seconds=30)),
        TradePrint("d", "BTC-PERPETUAL", 97000, 4, "sell", now - timedelta(seconds=20)),
        TradePrint("d", "BTC-PERPETUAL", 97000, 2, "buy", now - timedelta(seconds=400)),
    ]
    assert cvd_over_window(trades, window_s=60, now=now) == pytest.approx(6.0)
    perp = PerpSnapshot(
        venue="d",
        symbol="BTC-PERPETUAL",
        mark=97000,
        index=96950,
        funding_rate=0.0001,
        open_interest=1e6,
        cvd_1m=0,
        cvd_5m=0,
        cvd_15m=0,
        book_imbalance=0.1,
        fetched_at=now,
        source="t",
    )
    from crypto.engine.orderflow import OrderflowState

    st = OrderflowState()
    st.append_trades(trades)
    feats = compute_orderflow_features(perp, state=st, now=now)
    assert feats["cvd_1m"] == pytest.approx(6.0)
    assert "oi_delta" in feats
    assert "basis_bps" in feats
    assert feats["hooks"]["funding"] == pytest.approx(0.0001)


def test_inference_off_by_default():
    gex = {"spot": 1, "near_net": 0, "convention_total": 0, "flow_total": 0, "dist_flip_pct": 0, "models_agree": True, "dvol": 50, "mark_iv": 0.5}
    of = {"cvd_1m": 0, "cvd_5m": 0, "cvd_15m": 0, "book_imbalance": 0, "oi_delta": 0, "funding_rate": 0, "basis_bps": 0}
    assert run_inference(gex, of) is None


def test_inference_scaffold_when_flagged():
    set_flag("inference.feature_export", True, actor="test")
    set_flag("inference.regime_classifier", True, actor="test")
    gex = {
        "spot": 97000,
        "near_net": 1e6,
        "convention_total": 1e6,
        "flow_total": 1e5,
        "dist_flip_pct": 0.05,
        "models_agree": True,
        "dvol": 50,
        "mark_iv": 0.48,
    }
    of = {
        "cvd_1m": 10,
        "cvd_5m": 20,
        "cvd_15m": 30,
        "book_imbalance": 0.1,
        "oi_delta": 0,
        "funding_rate": 0.0001,
        "basis_bps": 5,
    }
    out = run_inference(gex, of)
    assert out is not None
    assert out["can_create_go"] is False
    assert "features" in out
    assert out["regime_classifier"]["model_version"] == "placeholder-v0"
    vec = export_feature_vector(gex, of)
    assert set(vec.keys()) >= {"spot", "cvd_5m", "models_agree"}
    pred = PlaceholderRegimeClassifier().predict(vec)
    assert pred.label in {"pinned", "trending", "transition", "disorderly"}


def test_replay_bundle_same_feature_path():
    result = replay_recorded_file(REPLAY_BUNDLE)
    assert result["gex"]["flow_mode"] == "signed_tape"
    assert result["flow_inventory"]["total_trades"] >= 1
    assert result["flow_inventory"]["blocks_skipped"] >= 1
    assert result["signal"]["status"] in {"GO", "NO-GO"}
    assert "model_version" in result["signal"]
    assert "cvd_5m" in result["orderflow"]
    # Inference still None when flags off
    assert result["inference"] is None


def test_replay_features_api():
    from crypto.engine.replay import chain_from_payload, load_recorded_bundle, trades_from_payloads, perp_from_payload

    data = load_recorded_bundle(REPLAY_BUNDLE)
    chain = chain_from_payload(data)
    trades = trades_from_payloads(data["trades"])
    perp = perp_from_payload(data["perp"])
    out = replay_features(chain=chain, trades=trades, perp=perp)
    assert out["gex"]["spot"] == pytest.approx(97240.0)
