"""Load synthetic option-chain fixtures shared by mock providers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crypto.config import DEFAULT_UNDERLYING, get_asset_config

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
DEFAULT_FIXTURE_PATH = FIXTURES_DIR / "btc_chain.json"


def fixture_path_for(underlying: str | None = None) -> Path:
    meta = get_asset_config(underlying or DEFAULT_UNDERLYING)
    name = str(meta.get("fixture") or f"{(underlying or DEFAULT_UNDERLYING).lower()}_chain.json")
    path = FIXTURES_DIR / name
    if path.is_file():
        return path
    # Fall back to BTC fixture if an asset file is missing
    return DEFAULT_FIXTURE_PATH


def load_fixture(underlying: str | None = None) -> dict[str, Any]:
    # Not cached: fixtures may change under tests / hot reload.
    path = fixture_path_for(underlying)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
