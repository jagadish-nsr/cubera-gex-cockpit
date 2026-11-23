"""Local Gemma runtime client (Ollama-compatible) + mock path.

No tools. Fail-soft. Off the order path. Flag-gated by ``intel.gemma``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from crypto.config import get_flag, load_config

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma2:12b"
DEFAULT_TIMEOUT_S = 8.0

# Injected by tests — (messages, kwargs) → text | None
_COMPLETE_HOOK: Callable[..., Optional[str]] | None = None


def set_complete_hook(hook: Callable[..., Optional[str]] | None) -> None:
    """Test seam: replace the HTTP completion path."""
    global _COMPLETE_HOOK
    _COMPLETE_HOOK = hook


def gemma_config() -> dict[str, Any]:
    """Resolved Gemma settings from YAML + env."""
    cfg = load_config().get("gemma") or {}
    base = (
        os.environ.get("CRYPTO_GEMMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or os.environ.get("OLLAMA_BASE_URL")
        or cfg.get("base_url")
        or DEFAULT_BASE_URL
    )
    model = (
        os.environ.get("CRYPTO_GEMMA_MODEL")
        or os.environ.get("OLLAMA_MODEL")
        or cfg.get("model")
        or DEFAULT_MODEL
    )
    timeout_raw = os.environ.get("CRYPTO_GEMMA_TIMEOUT_S") or cfg.get("timeout_s")
    try:
        timeout_s = float(timeout_raw) if timeout_raw is not None else DEFAULT_TIMEOUT_S
    except (TypeError, ValueError):
        timeout_s = DEFAULT_TIMEOUT_S
    mock = cfg.get("mock_when_unavailable")
    if mock is None:
        mock = True
    return {
        "base_url": str(base).rstrip("/"),
        "model": str(model).strip() or DEFAULT_MODEL,
        "timeout_s": max(0.5, float(timeout_s)),
        "mock_when_unavailable": bool(mock),
        "flag": get_flag("intel.gemma", default=False),
    }


def gemma_enabled() -> bool:
    return bool(get_flag("intel.gemma", default=False))


def gemma_available(*, probe: bool = False) -> bool:
    """True when flag is on and (hook set or optional live probe succeeds)."""
    if not gemma_enabled():
        return False
    if _COMPLETE_HOOK is not None:
        return True
    if not probe:
        # Without a probe, treat as "may be available" — callers still timeout-fallback.
        return True
    cfg = gemma_config()
    try:
        import requests

        r = requests.get(f"{cfg['base_url']}/api/tags", timeout=min(2.0, cfg["timeout_s"]))
        if not r.ok:
            return False
        models = (r.json() or {}).get("models") or []
        want = cfg["model"].split(":")[0].lower()
        for m in models:
            name = str((m or {}).get("name") or "").lower()
            if want and want in name:
                return True
            if cfg["model"].lower() in name:
                return True
        # Runtime up but model not pulled — still "available" for mock path decision
        return bool(models) or True
    except Exception as exc:  # noqa: BLE001
        log.debug("gemma probe failed: %s", exc)
        return False


def complete_json(
    messages: list[dict[str, str]],
    *,
    purpose: str,
    temperature: float = 0.1,
    max_tokens: int = 400,
) -> Optional[str]:
    """Return raw model text or None on timeout / error. Never raises."""
    if not gemma_enabled():
        return None
    cfg = gemma_config()
    if _COMPLETE_HOOK is not None:
        try:
            return _COMPLETE_HOOK(
                messages,
                purpose=purpose,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=cfg["timeout_s"],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("gemma hook %s failed: %s", purpose, exc)
            return None
    return _complete_ollama(
        messages,
        purpose=purpose,
        temperature=temperature,
        max_tokens=max_tokens,
        base_url=cfg["base_url"],
        model=cfg["model"],
        timeout_s=cfg["timeout_s"],
    )


def _complete_ollama(
    messages: list[dict[str, str]],
    *,
    purpose: str,
    temperature: float,
    max_tokens: int,
    base_url: str,
    model: str,
    timeout_s: float,
) -> Optional[str]:
    url = f"{base_url}/api/chat"
    clean = [
        {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
        for m in (messages or [])
        if isinstance(m, dict) and str(m.get("content") or "").strip()
    ]
    if not clean:
        return None
    # No tools — chat only, format json when runtime supports it
    body: dict[str, Any] = {
        "model": model,
        "messages": clean,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": float(temperature),
            "num_predict": int(max_tokens),
        },
    }
    try:
        import requests

        r = requests.post(url, json=body, timeout=float(timeout_s))
        if not r.ok:
            log.warning(
                "gemma ollama %s HTTP %s: %s",
                purpose,
                r.status_code,
                (r.text or "")[:180],
            )
            return None
        data = r.json() or {}
        msg = data.get("message") or {}
        out = str(msg.get("content") or "").strip()
        if not out:
            out = str(msg.get("reasoning") or "").strip()
        return out or None
    except Exception as exc:  # noqa: BLE001
        log.warning("gemma ollama %s failed: %s", purpose, exc)
        return None
