"""commentary/config_loader.py — Fast, cached loader for commentary.yaml."""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "commentary.yaml"


@functools.lru_cache(maxsize=1)
def load_commentary_config() -> dict[str, Any]:
    """Load and parse config/commentary.yaml. Returns dict. Cached in memory."""
    if not CONFIG_PATH.exists():
        log.warning("commentary.yaml not found at %s; returning empty defaults", CONFIG_PATH)
        return {}

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            return cfg
    except Exception as exc:
        log.error("Failed to load commentary config: %s", exc)
        return {}


get_commentary_config = load_commentary_config


def get_detector_config(detector_name: str) -> dict[str, Any]:
    """Return specific configuration sub-dict for a given detector."""
    cfg = load_commentary_config()
    detectors = cfg.get("detectors", {})
    return detectors.get(detector_name, {})


def get_bites_config() -> dict[str, Any]:
    """Return bites configuration."""
    cfg = load_commentary_config()
    return cfg.get("bites", {})


def get_budget_config() -> dict[str, Any]:
    """Return budget and cooldown rules."""
    cfg = load_commentary_config()
    return cfg.get("budget", {})


def get_forbidden_phrases() -> list[str]:
    """Return list of forbidden words and urgency imperatives."""
    bites_cfg = get_bites_config()
    return bites_cfg.get("forbidden_phrases", [
        "sure shot", "guaranteed", "can't lose", "buy now", "sell now",
        "risk-free", "easy money", "hurry", "last chance",
    ])
