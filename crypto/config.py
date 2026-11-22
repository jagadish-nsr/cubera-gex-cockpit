"""Load CryptoCubera YAML config + env overrides."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "crypto.yaml"
DEFAULT_UNDERLYING = "BTC"
KNOWN_ASSETS = ("BTC", "ETH", "SOL")

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

    # Active underlying override (BTC | ETH | SOL)
    env_u = (os.environ.get("CRYPTO_UNDERLYING") or "").strip().upper()
    if env_u:
        raw["underlying"] = env_u

    if os.environ.get("CRYPTO_LIVE_SPIKE_DERIBIT", "").lower() in {"1", "true", "yes"}:
        raw.setdefault("live_spike", {})["deribit_ticker"] = True
        raw.setdefault("live_spike", {})["deribit_ws"] = True

    if os.environ.get("CRYPTO_LIVE_FEEDS", "").lower() in {"1", "true", "yes", "on"}:
        # Master live gate — still needs feeds.deribit / feeds.delta flags on.
        raw.setdefault("live_spike", {})["deribit_ws"] = True
        raw.setdefault("live_spike", {})["delta_ws"] = True

    if os.environ.get("CRYPTO_LIVE_SPIKE_DELTA", "").lower() in {"1", "true", "yes", "on"}:
        raw.setdefault("live_spike", {})["delta_ws"] = True

    # Ensure BTC remains selectable even if YAML omitted the block
    assets = raw.setdefault("assets", {})
    if "BTC" not in assets:
        assets["BTC"] = {
            "label": "Bitcoin",
            "feature_flag": "assets.btc",
            "fixture": "btc_chain.json",
            "currency": "BTCUSD",
            "deribit_currency": "BTC",
            "deribit_perp": "BTC-PERPETUAL",
            "delta_symbols": ["BTCUSD"],
            "binance_perp": "BTCUSDT",
            "cryptopanic_currencies": "BTC",
        }
    flags.setdefault("assets.btc", True)

    _CONFIG = raw
    return raw


def get_flag(name: str, default: bool = False) -> bool:
    cfg = load_config()
    return bool(cfg.get("feature_flags", {}).get(name, default))


def set_flag(name: str, value: bool, *, actor: str = "operator") -> dict[str, Any]:
    """Mutate in-memory flag (and persist audit via store when available)."""
    cfg = load_config()
    flags = cfg.setdefault("feature_flags", {})
    # BTC asset gate stays on — cockpit always needs a default underlying.
    if name == "assets.btc" and not value:
        return {
            "name": name,
            "previous": bool(flags.get(name, True)),
            "value": True,
            "actor": actor,
            "note": "assets.btc cannot be disabled (BTC is the default underlying)",
        }
    prev = bool(flags.get(name, False))
    flags[name] = bool(value)
    # Turning off the active asset falls back to BTC
    if name.startswith("assets.") and not value:
        symbol = name.split(".", 1)[1].upper()
        if str(cfg.get("underlying") or "").upper() == symbol:
            cfg["underlying"] = DEFAULT_UNDERLYING
    return {"name": name, "previous": prev, "value": bool(value), "actor": actor}


def list_flags() -> dict[str, bool]:
    return dict(load_config().get("feature_flags") or {})


def _asset_flag_name(symbol: str, meta: dict[str, Any] | None = None) -> str:
    meta = meta or {}
    return str(meta.get("feature_flag") or f"assets.{symbol.lower()}")


def is_asset_enabled(symbol: str) -> bool:
    """BTC is always available; ETH/SOL require their feature flag."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return False
    cfg = load_config()
    meta = (cfg.get("assets") or {}).get(sym) or {}
    flag = _asset_flag_name(sym, meta)
    if sym == DEFAULT_UNDERLYING:
        return get_flag(flag, default=True)
    return get_flag(flag, default=False)


def get_asset_config(symbol: str | None = None) -> dict[str, Any]:
    """Return provider params for an underlying (defaults for unknown keys)."""
    cfg = load_config()
    sym = (symbol or get_active_underlying()).strip().upper()
    meta = dict((cfg.get("assets") or {}).get(sym) or {})
    meta.setdefault("label", sym)
    meta.setdefault("feature_flag", f"assets.{sym.lower()}")
    meta.setdefault("fixture", f"{sym.lower()}_chain.json")
    meta.setdefault("deribit_currency", sym)
    meta.setdefault("deribit_perp", f"{sym}-PERPETUAL")
    meta.setdefault("delta_symbols", [f"{sym}USD"])
    meta.setdefault("binance_perp", f"{sym}USDT")
    meta.setdefault("cryptopanic_currencies", sym)
    # Quote / Manual Decision currency = Delta pair (BTCUSD / ETHUSD / SOLUSD).
    deltas = list(meta.get("delta_symbols") or [f"{sym}USD"])
    meta.setdefault("currency", deltas[0] if deltas else f"{sym}USD")
    meta["symbol"] = sym
    meta["enabled"] = is_asset_enabled(sym)
    return meta


def get_quote_currency(symbol: str | None = None) -> str:
    """Delta India quote pair for the underlying (e.g. BTCUSD).

    Used on GO cards and Manual Decision copy — not Deribit's BTC/ETH/SOL
    option-currency code, and never an equity index symbol.
    """
    meta = get_asset_config(symbol)
    cur = str(meta.get("currency") or "").strip().upper()
    if cur:
        return cur
    deltas = list(meta.get("delta_symbols") or [])
    if deltas:
        return str(deltas[0]).strip().upper()
    sym = str(meta.get("symbol") or DEFAULT_UNDERLYING).strip().upper()
    return f"{sym}USD"


def list_assets() -> list[dict[str, Any]]:
    """Catalog of configured underlyings with flag/active state."""
    cfg = load_config()
    active = get_active_underlying()
    assets_cfg = cfg.get("assets") or {}
    symbols = list(assets_cfg.keys()) or list(KNOWN_ASSETS)
    # Stable order: BTC, ETH, SOL, then any extras
    order = {s: i for i, s in enumerate(KNOWN_ASSETS)}
    symbols = sorted(symbols, key=lambda s: order.get(s, 100 + len(s)))
    out: list[dict[str, Any]] = []
    for sym in symbols:
        meta = get_asset_config(sym)
        out.append(
            {
                "symbol": sym,
                "label": meta.get("label") or sym,
                "feature_flag": meta.get("feature_flag"),
                "enabled": bool(meta.get("enabled")),
                "active": sym == active,
                "currency": meta.get("currency"),
                "deribit_currency": meta.get("deribit_currency"),
                "deribit_perp": meta.get("deribit_perp"),
                "delta_symbols": list(meta.get("delta_symbols") or []),
                "fixture": meta.get("fixture"),
            }
        )
    return out


def get_active_underlying() -> str:
    cfg = load_config()
    raw = str(cfg.get("underlying") or DEFAULT_UNDERLYING).strip().upper()
    if is_asset_enabled(raw):
        return raw
    return DEFAULT_UNDERLYING


def set_underlying(symbol: str, *, actor: str = "operator") -> dict[str, Any]:
    """Switch active underlying. Raises ValueError if unknown or flag-off."""
    sym = (symbol or "").strip().upper()
    cfg = load_config()
    assets = cfg.get("assets") or {}
    if sym not in assets and sym not in KNOWN_ASSETS:
        raise ValueError(f"Unknown underlying {sym!r}; known: {', '.join(KNOWN_ASSETS)}")
    if not is_asset_enabled(sym):
        flag = _asset_flag_name(sym, assets.get(sym) or {})
        raise ValueError(f"Underlying {sym} is disabled ({flag}=false)")
    prev = get_active_underlying()
    cfg["underlying"] = sym
    return {
        "underlying": sym,
        "previous": prev,
        "actor": actor,
        "asset": get_asset_config(sym),
    }
