"""Local Gemma 12B language layer for CryptoCubera.

Classifies headlines, words GO-card explanations, and builds digests.
Schema-validated JSON only. Timeout / unavailable → keyword or template
fallback. No tools. Never creates a GO or invents prices — numbers come
from structured code fields only.
"""

from crypto.gemma.classify import classify_with_gemma
from crypto.gemma.client import gemma_available, gemma_config
from crypto.gemma.digests import build_digest
from crypto.gemma.go_prose import explain_go_card

__all__ = [
    "build_digest",
    "classify_with_gemma",
    "explain_go_card",
    "gemma_available",
    "gemma_config",
]
