"""Drop broker tokens and bearer strings before they reach a log or the ledger."""

from __future__ import annotations

import re

_SECRET_KEYS = frozenset({
    "token",
    "access_token",
    "authorization",
    "password",
    "client_secret",
    "api_key",
    "secret",
    "refresh_token",
    "dhan_access_token",
})
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{8,}=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def _secret_key(key: object) -> bool:
    name = str(key).lower()
    return name in _SECRET_KEYS or name.endswith("_token") or name.endswith("_secret")


def redact_text(value: str) -> str:
    value = _BEARER.sub("Bearer [redacted]", value)
    return _JWT.sub("[redacted]", value)


def redact_mapping(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        if _secret_key(key):
            out[key] = "[redacted]"
        else:
            out[key] = redact_value(value)
    return out


def redact_value(value):
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value
