"""Live signal path applies the 2s stale hard veto."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from crypto.engine.signals import build_signal_card
from crypto.providers.types import PerpSnapshot


def _perp(age_s: float) -> PerpSnapshot:
    now = datetime.now(timezone.utc)
    return PerpSnapshot(
        venue="test",
        symbol="BTC-PERP",
        mark=60000,
        index=60000,
        funding_rate=0.0,
        open_interest=1.0,
        cvd_1m=0,
        cvd_5m=0,
        cvd_15m=0,
        book_imbalance=0,
        fetched_at=now - timedelta(seconds=age_s),
        source="test",
    )


def test_live_path_stale_forces_nogo():
    now = datetime.now(timezone.utc)
    gex = {
        "models_agree": True,
        "regime": "positive_gamma",
        "source": "deribit-ws+rest",
        "spot": 60000,
        "flip": 59800,
        "call_wall": 62000,
        "put_wall": 58000,
        "magnet": 60000,
    }
    card = build_signal_card(
        gex,
        _perp(0.5),
        chain_fetched_at=now - timedelta(seconds=3.0),
        live_signal_path=True,
    )
    assert card["status"] == "NO-GO"
    assert any("stale" in v for v in card["veto_reasons"])
