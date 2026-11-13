"""Chaos / fault-injection host (Pillar G).

Isolated from the live cockpit. Never writes ``data/sentinel.db`` or live
journal DBs. See ``docs/chaos-drill.md``.
"""

from app.chaos.state import get_faults, reset_faults, set_faults

__all__ = ["get_faults", "set_faults", "reset_faults"]
