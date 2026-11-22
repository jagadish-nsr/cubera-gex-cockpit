"""Paper / stub execution venue — no live orders."""

from __future__ import annotations

from typing import Any

from crypto.providers.types import utcnow


class MockExecutionVenue:
    name = "mock"

    async def status(self) -> dict[str, Any]:
        return {
            "venue": "mock",
            "mode": "advisory",
            "can_trade": False,
            "note": "Foundation: no order placement. Kill switch disabled until wired.",
            "checked_at": utcnow().isoformat(),
        }
