"""Market intelligence: ingest → normalize → score → blackout hooks.

Never places trades. Advisory score / blackout only.
"""

from crypto.intel.blackout import blackout_active, blackout_summary
from crypto.intel.pipeline import run_intel_pipeline
from crypto.intel.schema import IntelEvent, event_to_dict, tier_for_score

__all__ = [
    "IntelEvent",
    "blackout_active",
    "blackout_summary",
    "event_to_dict",
    "run_intel_pipeline",
    "tier_for_score",
]
