"""Load synthetic BTC fixtures shared by mock providers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "btc_chain.json"


def load_fixture() -> dict[str, Any]:
    # Not cached: fixtures may change under tests / hot reload.
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
