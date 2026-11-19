"""commentary/narrator/client.py — Template narration with optional grounded LLM rewrite."""

from __future__ import annotations

import logging
from typing import Any

from commentary.models import Event, SuggestionBite
from commentary.config_loader import get_commentary_config
from commentary.narrator.templates import render_event_template, render_bite_template
from commentary.narrator.verifier import (
    _extract_all_numbers_from_dict,
    _extract_numbers_from_text,
    _matches_any_fact,
    verify_commentary,
)

logger = logging.getLogger(__name__)

_EXEC_FORBIDDEN = [
    "sure shot",
    "guaranteed",
    "can't lose",
    "cant lose",
    "buy now",
    "sell now",
    "risk-free",
    "risk free",
    "easy money",
    "hurry",
    "last chance",
    "to the moon",
    "free money",
    "blind buy",
    "place order",
    "market order",
    "auto-exec",
    "auto exec",
    "enter long",
    "enter short",
]


class NarratorClient:
    """Narrates market events into structured headlines and prose.

    Deterministic templates are always the baseline. When LLM is enabled and
    ready, prose may be rewritten for the desk and must pass fact verification.
    """

    def __init__(self, enable_llm: bool | None = None) -> None:
        cfg = get_commentary_config()
        narrator_cfg = cfg.get("narrator", {})
        self.enable_llm = (
            enable_llm
            if enable_llm is not None
            else bool(narrator_cfg.get("enable_llm_enhancement", False))
        )
        self.fallback_on_error = bool(narrator_cfg.get("fallback_to_template_on_error", True))
        self.max_words = int(narrator_cfg.get("max_words_per_entry", 80) or 80)
        self.last_text_source = "heuristic"
        self.last_llm_model: str | None = None

    def _llm_ready(self) -> bool:
        if not self.enable_llm:
            return False
        try:
            from analytics.llm_client import llm_enabled
            return bool(llm_enabled("commentary"))
        except Exception:
            return False

    def _verify_story(
        self,
        headline: str,
        body: str,
        event: Event,
        context: dict[str, Any] | None,
    ) -> tuple[bool, list[str]]:
        facts: dict[str, Any] = dict(event.facts or {})
        if isinstance(context, dict):
            for k, v in context.items():
                if k not in facts:
                    facts[k] = v
        text = f"{headline} {body}"
        ok, violations = verify_commentary(
            text,
            facts,
            spot_delta=_spot_delta(facts, context),
            custom_forbidden=_EXEC_FORBIDDEN,
            strict_numeric=False,
        )
        fact_nums = _extract_all_numbers_from_dict(facts)
        fact_nums.extend([0.0, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0, 65.0, 100.0])
        for num in _extract_numbers_from_text(text):
            abs_n = abs(num)
            if abs_n < 100.0 or 2020.0 <= abs_n <= 2035.0:
                continue
            if not _matches_any_fact(num, fact_nums):
                violations.append(f"Unverified number in commentary: {num}")
                ok = False
        return ok, violations

    def narrate(
        self,
        event: Event,
        context: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Generate verified (headline, body) for an event.

        Falls back cleanly to deterministic templates if verification or generation fails.
        Sets ``last_text_source`` to ``genai`` or ``heuristic``.
        """
        tmpl_headline, tmpl_body = render_event_template(event, context)
        self.last_text_source = "heuristic"
        self.last_llm_model = None

        if not self._llm_ready():
            return tmpl_headline, tmpl_body

        try:
            from analytics.llm_client import complete_chat, model_name
            from analytics.llm_prompts import (
                commentary_story_messages,
                parse_commentary_story_rewrite,
            )

            raw = complete_chat(
                commentary_story_messages(
                    event_type=str(event.type),
                    event_facts=dict(event.facts or {}),
                    template_headline=tmpl_headline,
                    template_body=tmpl_body,
                    session_context=context,
                    max_words=self.max_words,
                ),
                purpose="commentary_story",
                temperature=0.2,
                max_tokens=420,
                timeout_s=90.0,
            )
            parsed = parse_commentary_story_rewrite(raw or "")
            if not parsed:
                return tmpl_headline, tmpl_body
            llm_headline, llm_body = parsed
            is_valid, violations = self._verify_story(llm_headline, llm_body, event, context)
            if not is_valid:
                logger.warning(
                    "Narrator verification failed (%s); falling back to template.",
                    violations[:4],
                )
                return tmpl_headline, tmpl_body
            self.last_text_source = "genai"
            self.last_llm_model = model_name()
            return llm_headline, llm_body
        except Exception as exc:
            logger.error("Narrator generation error: %s", exc)
            if self.fallback_on_error:
                return tmpl_headline, tmpl_body
            raise

    def narrate_bite(self, bite: SuggestionBite) -> str:
        """Generate non-imperative explanation for a suggestion bite."""
        text = render_bite_template(bite)
        is_valid, violations = verify_commentary(text, bite.levels)
        if not is_valid:
            logger.warning("Bite verification found issues: %s", violations)
        return text


def _spot_delta(facts: dict[str, Any], context: dict[str, Any] | None) -> float | None:
    for src in (facts, context or {}):
        if not isinstance(src, dict):
            continue
        if "spot_delta" in src:
            try:
                return float(src["spot_delta"])
            except (TypeError, ValueError):
                pass
        try:
            spot = float(src.get("spot") or 0)
            prior = float(src.get("prior_close") or src.get("prev_close") or 0)
            if spot > 0 and prior > 0:
                return spot - prior
        except (TypeError, ValueError):
            pass
    return None
