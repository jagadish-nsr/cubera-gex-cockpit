"""Trace and symbol scope for the current thread of work."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_trace_id: ContextVar[str | None] = ContextVar("cubera_trace_id", default=None)
_symbol: ContextVar[str | None] = ContextVar("cubera_symbol", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def current() -> dict:
    return {"trace_id": _trace_id.get(), "symbol": _symbol.get()}


@contextmanager
def bind(*, trace_id: str | None = None, symbol: str | None = None):
    """Stamp logs emitted inside the block. A new thread does not inherit this."""
    tokens = []
    if trace_id is not None:
        tokens.append((_trace_id, _trace_id.set(trace_id)))
    if symbol is not None:
        tokens.append((_symbol, _symbol.set(symbol)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
