"""Gamma-blast symbol gate: each index is judged on its own nearest expiry."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import scripts.log_gamma_blast as blast


def test_default_symbols_are_the_three_indices():
    assert blast.resolve_symbols(None) == ("NIFTY", "BANKNIFTY", "SENSEX")
    assert blast.resolve_symbols("SENSEX") == ("SENSEX",)
    assert blast.resolve_symbols("banknifty, sensex") == ("BANKNIFTY", "SENSEX")


def test_sensex_expiry_does_not_pull_nifty_or_banknifty(monkeypatch):
    """2026-10-29 is a Thursday. Only the Sensex chain expires that day."""
    seen = []
    fetched = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)  # 13:30 IST

    def load(symbol=None, **_kwargs):
        seen.append(symbol)
        nearest = date(2026, 10, 29) if symbol == "SENSEX" else date(2026, 11, 3)
        return SimpleNamespace(
            symbol=symbol, expiries=[nearest], fetched_at=fetched,
            source="nse", levels=SimpleNamespace(spot=80000.0, magnet=80000.0),
        )

    monkeypatch.setattr(blast, "load_dashboard", load)
    monkeypatch.setattr(blast, "build_strike_rows", lambda data: [{"strike": 1}])
    written = []
    monkeypatch.setattr(blast, "append_strike_rows", lambda *a, **k: written.append(k.get("symbol") or a))

    messages = blast.capture_symbols()
    assert seen == ["NIFTY", "BANKNIFTY", "SENSEX"]
    assert "not an expiry day" in messages[0]
    assert "not an expiry day" in messages[1]
    assert "skipped" not in messages[2]
    assert written == ["SENSEX"]
