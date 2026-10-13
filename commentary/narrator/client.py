"""commentary/narrator/client.py — Narrator Client providing Deterministic Template narration with verification."""

from __future__ import annotations

import logging
from typing import Any
from commentary.models import Event, SuggestionBite
from commentary.config_loader import get_commentary_config
from commentary.narrator.templates import render_event_template, render_bite_template
from commentary.narrator.verifier import verify_commentary

logger = logging.getLogger(__name__)


class NarratorClient:
    """Narrates market events into structured headlines and prose.
    
    Defaults to 100% deterministic template generation for 0ms latency and 0 hallucination risk.
    """

    def __init__(self, enable_llm: bool | None = None) -> None:
        cfg = get_commentary_config()
        narrator_cfg = cfg.get("narrator", {})
        self.enable_llm = enable_llm if enable_llm is not None else narrator_cfg.get("enable_llm_enhancement", False)
        self.fallback_on_error = narrator_cfg.get("fallback_to_template_on_error", True)

    def narrate(self, event: Event, context: dict[str, Any] | None = None) -> tuple[str, str]:
        """Generate verified (headline, body) for an event.

        Falls back cleanly to deterministic templates if verification or generation fails.
        """
        # Baseline deterministic generation
        tmpl_headline, tmpl_body = render_event_template(event, context)

        if not self.enable_llm:
            return tmpl_headline, tmpl_body

        # If LLM enhancement is enabled in the future:
        try:
            # Placeholder for optional LLM provider call
            # Generated prose must pass strict verification:
            llm_headline, llm_body = tmpl_headline, tmpl_body
            is_valid, violations = verify_commentary(f"{llm_headline} {llm_body}", event.facts)
            if not is_valid:
                logger.warning(f"Narrator verification failed ({violations}); falling back to template.")
                return tmpl_headline, tmpl_body
            return llm_headline, llm_body
        except Exception as exc:
            logger.error(f"Narrator generation error: {exc}")
            if self.fallback_on_error:
                return tmpl_headline, tmpl_body
            raise

    def narrate_bite(self, bite: SuggestionBite) -> str:
        """Generate non-imperative explanation for a suggestion bite."""
        text = render_bite_template(bite)
        # Verify no forbidden phrases in bite text
        is_valid, violations = verify_commentary(text, bite.levels)
        if not is_valid:
            logger.warning(f"Bite verification found issues: {violations}")
        return text
