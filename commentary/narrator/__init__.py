"""commentary.narrator — Deterministic templates and prose verification."""

from __future__ import annotations

from commentary.narrator.templates import (
    render_event_template,
    render_bite_template,
    format_story_entry,
    extract_topic_header,
)
from commentary.narrator.verifier import verify_commentary

__all__ = [
    "render_event_template",
    "render_bite_template",
    "format_story_entry",
    "extract_topic_header",
    "verify_commentary",
]
