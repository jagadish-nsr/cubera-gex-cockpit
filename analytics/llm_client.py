"""Shared chat client for grounded prose rewrites (Ollama local + optional Gemini).

Default provider is local Ollama (``http://localhost:11434``). Gemini remains
available via ``CUBERA_LLM_PROVIDER=gemini`` + ``GEMINI_API_KEY``.

Feature-gated and fail-soft: callers keep their deterministic template on miss.
Never log API keys. Prompts are the caller's responsibility.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma4:12b"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name, "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def provider() -> str:
    """``ollama`` (default) or ``gemini``."""
    raw = str(os.environ.get("CUBERA_LLM_PROVIDER") or os.environ.get("LLM_PROVIDER") or "ollama")
    p = raw.strip().lower()
    if p in ("google", "google_ai", "google-ai"):
        return "gemini"
    if p in ("ollama", "local", "gemini"):
        return p if p != "local" else "ollama"
    return "ollama"


def ollama_host() -> str:
    return (
        str(os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_HOST)
        .strip()
        .rstrip("/")
        or DEFAULT_OLLAMA_HOST
    )


def gemini_api_key() -> str:
    return (
        str(os.environ.get("GEMINI_API_KEY") or "").strip()
        or str(os.environ.get("GOOGLE_API_KEY") or "").strip()
    )


def api_key() -> str:
    """Back-compat alias — Gemini key only (Ollama needs none)."""
    return gemini_api_key()


def model_name() -> str:
    p = provider()
    if p == "gemini":
        return (
            str(os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip()
            or DEFAULT_GEMINI_MODEL
        )
    return (
        str(os.environ.get("OLLAMA_MODEL") or os.environ.get("LLM_MODEL") or DEFAULT_OLLAMA_MODEL)
        .strip()
        or DEFAULT_OLLAMA_MODEL
    )


def llm_enabled(feature: str) -> bool:
    """Master ``CUBERA_LLM`` + provider readiness + optional feature gate.

    Feature examples: ``digest``, ``alert_tape`` → ``CUBERA_LLM_DIGEST``,
    ``CUBERA_LLM_ALERT_TAPE``. Unset feature flag → enabled when master is on.
    """
    if not _env_flag("CUBERA_LLM"):
        return False
    p = provider()
    if p == "gemini" and not gemini_api_key():
        return False
    # ollama: no key required; host is always defaulted
    feat = str(feature or "").strip().upper().replace("-", "_")
    if not feat:
        return True
    specific = f"CUBERA_LLM_{feat}"
    raw = os.environ.get(specific)
    if raw is None or str(raw).strip() == "":
        return True
    return _env_flag(specific)


def _messages_to_gemini(messages: list[dict[str, str]]) -> tuple[str | None, list[dict]]:
    system_parts: list[str] = []
    contents: list[dict] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user").lower()
        text = str(m.get("content") or "").strip()
        if not text:
            continue
        if role == "system":
            system_parts.append(text)
            continue
        g_role = "model" if role == "assistant" else "user"
        contents.append({"role": g_role, "parts": [{"text": text}]})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, contents


def _note_egress(purpose: str) -> None:
    try:
        from app.obs import metrics as obs_metrics
        obs_metrics.note_egress(f"llm_{purpose}")
    except Exception:  # noqa: BLE001
        pass


def _complete_ollama(
    messages: list[dict[str, str]],
    *,
    purpose: str,
    temperature: float,
    max_tokens: int,
    timeout_s: float,
) -> str | None:
    """Native Ollama ``/api/chat`` with ``think=false`` (avoids empty content on reasoning models)."""
    host = ollama_host()
    model = model_name()
    url = f"{host}/api/chat"
    clean = [
        {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
        for m in (messages or [])
        if isinstance(m, dict) and str(m.get("content") or "").strip()
    ]
    if not clean:
        return None
    body: dict[str, Any] = {
        "model": model,
        "messages": clean,
        "stream": False,
        "think": False,
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
                "llm_client ollama %s HTTP %s: %s",
                purpose,
                r.status_code,
                (r.text or "")[:180],
            )
            return None
        data = r.json() or {}
        msg = data.get("message") or {}
        out = str(msg.get("content") or "").strip()
        if not out:
            # Some builds still put text under reasoning when think cannot be disabled.
            out = str(msg.get("reasoning") or "").strip()
        if not out:
            return None
        _note_egress(purpose)
        return out
    except Exception as ex:  # noqa: BLE001
        log.warning("llm_client ollama %s failed: %s", purpose, ex)
        return None


def _complete_gemini(
    messages: list[dict[str, str]],
    *,
    purpose: str,
    temperature: float,
    max_tokens: int,
    timeout_s: float,
) -> str | None:
    key = gemini_api_key()
    if not key:
        return None
    system, contents = _messages_to_gemini(messages)
    if not contents:
        return None
    model = model_name()
    url = GEMINI_GENERATE_URL.format(model=model)
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": float(temperature),
            "maxOutputTokens": int(max_tokens),
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    try:
        import requests
        r = requests.post(
            url,
            params={"key": key},
            json=body,
            timeout=float(timeout_s),
        )
        if not r.ok:
            log.warning(
                "llm_client gemini %s HTTP %s: %s",
                purpose,
                r.status_code,
                (r.text or "")[:180],
            )
            return None
        data = r.json() or {}
        cands = data.get("candidates") or []
        if not cands:
            return None
        parts = ((cands[0] or {}).get("content") or {}).get("parts") or []
        texts = [str(p.get("text") or "").strip() for p in parts if isinstance(p, dict)]
        out = "\n".join(t for t in texts if t).strip()
        if not out:
            return None
        _note_egress(purpose)
        return out
    except Exception as ex:  # noqa: BLE001
        log.warning("llm_client gemini %s failed: %s", purpose, ex)
        return None


def complete_chat(
    messages: list[dict[str, str]],
    *,
    purpose: str,
    temperature: float = 0.2,
    max_tokens: int = 512,
    timeout_s: float | None = None,
) -> str | None:
    """Return model text or None on any failure. Never raises."""
    p = provider()
    if timeout_s is None:
        timeout_s = 90.0 if p == "ollama" else 8.0
    if p == "gemini":
        return _complete_gemini(
            messages,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=float(timeout_s),
        )
    return _complete_ollama(
        messages,
        purpose=purpose,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_s=float(timeout_s),
    )
