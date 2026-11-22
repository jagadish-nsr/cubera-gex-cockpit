"""Inference scaffold — feature export + placeholder regime classifier.

Off by default (``inference.regime_classifier``, ``inference.feature_export``).
No live model weights; interface only so walk-forward can plug in later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from crypto.config import get_flag


FEATURE_NAMES = [
    "spot",
    "near_net",
    "convention_total",
    "flow_total",
    "dist_flip_pct",
    "models_agree",
    "cvd_1m",
    "cvd_5m",
    "cvd_15m",
    "book_imbalance",
    "oi_delta",
    "funding_rate",
    "basis_bps",
    "dvol",
    "mark_iv",
]


@dataclass
class RegimePrediction:
    label: str  # pinned | trending | transition | disorderly | unknown
    probabilities: dict[str, float]
    model_version: str
    note: str = ""


@runtime_checkable
class RegimeClassifier(Protocol):
    model_version: str

    def predict(self, features: dict[str, float]) -> RegimePrediction: ...


class PlaceholderRegimeClassifier:
    """Deterministic stub — never creates a GO; advisory probability only."""

    model_version = "placeholder-v0"

    def predict(self, features: dict[str, float]) -> RegimePrediction:
        agree = features.get("models_agree", 0.0) >= 0.5
        dist = abs(features.get("dist_flip_pct") or 0.0)
        cvd = abs(features.get("cvd_5m") or 0.0)
        if not agree:
            label = "disorderly"
        elif dist < 0.15 and cvd < 30:
            label = "pinned"
        elif cvd >= 80:
            label = "trending"
        else:
            label = "transition"
        probs = {
            "pinned": 0.25,
            "trending": 0.25,
            "transition": 0.25,
            "disorderly": 0.25,
        }
        probs[label] = 0.55
        rem = (1.0 - 0.55) / 3.0
        for k in probs:
            if k != label:
                probs[k] = rem
        return RegimePrediction(
            label=label,
            probabilities=probs,
            model_version=self.model_version,
            note="Placeholder only — not calibrated; inference flag must be on to surface.",
        )


def export_feature_vector(
    gex: dict[str, Any],
    orderflow: dict[str, Any],
) -> dict[str, float]:
    """Point-in-time feature vector shared by live and replay paths."""
    return {
        "spot": float(gex.get("spot") or 0),
        "near_net": float(gex.get("near_net") or 0),
        "convention_total": float(gex.get("convention_total") or 0),
        "flow_total": float(gex.get("flow_total") or 0),
        "dist_flip_pct": float(gex.get("dist_flip_pct") or 0),
        "models_agree": 1.0 if gex.get("models_agree") else 0.0,
        "cvd_1m": float(orderflow.get("cvd_1m") or 0),
        "cvd_5m": float(orderflow.get("cvd_5m") or 0),
        "cvd_15m": float(orderflow.get("cvd_15m") or 0),
        "book_imbalance": float(orderflow.get("book_imbalance") or 0),
        "oi_delta": float(orderflow.get("oi_delta") or 0),
        "funding_rate": float(orderflow.get("funding_rate") or 0),
        "basis_bps": float(orderflow.get("basis_bps") or 0),
        "dvol": float(gex.get("dvol") or 0),
        "mark_iv": float(gex.get("mark_iv") or 0),
    }


def run_inference(
    gex: dict[str, Any],
    orderflow: dict[str, Any],
    *,
    classifier: Optional[RegimeClassifier] = None,
) -> dict[str, Any] | None:
    """Return inference payload when flags allow; else None."""
    export_on = get_flag("inference.feature_export", default=False)
    clf_on = get_flag("inference.regime_classifier", default=False)
    if not export_on and not clf_on:
        return None

    features = export_feature_vector(gex, orderflow)
    out: dict[str, Any] = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "feature_names": list(FEATURE_NAMES),
    }
    if export_on:
        out["features"] = features
    if clf_on:
        clf = classifier or PlaceholderRegimeClassifier()
        pred = clf.predict(features)
        out["regime_classifier"] = {
            "label": pred.label,
            "probabilities": pred.probabilities,
            "model_version": pred.model_version,
            "note": pred.note,
        }
    out["advisory_only"] = True
    out["can_create_go"] = False  # hard rule: inference never creates GO
    return out
