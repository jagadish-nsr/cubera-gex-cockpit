"""Load CryptoCubera YAML config + env overrides."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "crypto.yaml"

_CONFIG: dict[str, Any] | None = None


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = deepcopy(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def load_config(path: Path | str | None = None, *, reload: bool = False) -> dict[str, Any]:
    """Load config from YAML. Env CRYPTO_CONFIG overrides path; CRYPTO_* flags override."""
    global _CONFIG
    if _CONFIG is not None and not reload and path is None:
        return _CONFIG

    cfg_path = Path(path or os.environ.get("CRYPTO_CONFIG", DEFAULT_CONFIG_PATH))
    with open(cfg_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    # Env overrides for common flags: CRYPTO_FLAG_FEEDS_DERIBIT=1 → feeds.deribit
    flags = dict(raw.get("feature_flags") or {})
    for key, val in os.environ.items():
        if key.startswith("CRYPTO_FLAG_"):
            dotted = key[len("CRYPTO_FLAG_") :].lower().replace("__", ".").replace("_", ".", 1)
            # CRYPTO_FLAG_FEEDS_DERIBIT → feeds.deribit (first underscore → dot)
            parts = key[len("CRYPTO_FLAG_") :].lower().split("_", 1)
            if len(parts) == 2:
                dotted = f"{parts[0]}.{parts[1]}"
            else:
                dotted = parts[0]
            flags[dotted] = val.strip().lower() in {"1", "true", "yes", "on"}
    raw["feature_flags"] = flags

    if os.environ.get("CRYPTO_DB_URL"):
        raw.setdefault("database", {})["url"] = os.environ["CRYPTO_DB_URL"]

    if os.environ.get("CRYPTO_LIVE_SPIKE_DERIBIT", "").lower() in {"1", "true", "yes"}:
        raw.setdefault("live_spike", {})["deribit_ticker"] = True
        raw.setdefault("live_spike", {})["deribit_ws"] = True

    if os.environ.get("CRYPTO_LIVE_FEEDS", "").lower() in {"1", "true", "yes", "on"}:
        # Master live gate — still needs feeds.deribit / feeds.delta flags on.
        raw.setdefault("live_spike", {})["deribit_ws"] = True
        raw.setdefault("live_spike", {})["delta_ws"] = True

    if os.environ.get("CRYPTO_LIVE_SPIKE_DELTA", "").lower() in {"1", "true", "yes", "on"}:
        raw.setdefault("live_spike", {})["delta_ws"] = True

    _CONFIG = raw
    return raw


def get_flag(name: str, default: bool = False) -> bool:
    cfg = load_config()
    return bool(cfg.get("feature_flags", {}).get(name, default))


def set_flag(name: str, value: bool, *, actor: str = "operator") -> dict[str, Any]:
    """Mutate in-memory flag (and persist audit via store when available)."""
    cfg = load_config()
    flags = cfg.setdefault("feature_flags", {})
    prev = bool(flags.get(name, False))
    flags[name] = bool(value)
    return {"name": name, "previous": prev, "value": bool(value), "actor": actor}


def list_flags() -> dict[str, bool]:
    return dict(load_config().get("feature_flags") or {})
