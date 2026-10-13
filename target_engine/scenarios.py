"""T2 Scenario Scoring Engine.

Combines extracted point-in-time features into calibrated or heuristic probabilities
for Bull, Base (range), and Bear scenarios using softmax logits with conviction scaling.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional
import numpy as np

from target_engine.config import DEFAULT_CONFIG, TargetEngineConfig
from target_engine.contracts import FeatureVector, ScenarioProb, ScenarioSet


def score_scenarios(
    feature_vector: FeatureVector,
    config: TargetEngineConfig = DEFAULT_CONFIG,
    calibrated_model: Optional[Any] = None,
) -> ScenarioSet:
    """Compute Bull, Base, and Bear scenario probabilities from a FeatureVector.

    Stage A: Heuristic baseline with softmax scoring and conviction modulation.
    Stage B: Machine learning calibrated model if provided.
    """
    fv = feature_vector
    features = fv.features

    # 1. Compute directional score D = sum(w_i * F_i)
    weights = config.feature_weights
    available_w_sum = sum(abs(weights[k]) for k in weights if k in features)
    total_abs_w = available_w_sum if available_w_sum > 0 else (sum(abs(w) for w in weights.values()) or 1.0)

    contributions: Dict[str, float] = {}
    directional_score = 0.0

    for fname, w in weights.items():
        if fname in features:
            f_val = float(features[fname])
            norm_w = w / total_abs_w
            contrib = norm_w * f_val
            contributions[fname] = contrib
            directional_score += contrib

    directional_score = float(np.clip(directional_score, -1.0, 1.0))

    # 2. Compute Conviction / Agreement scalar C in [0, 1]
    # High when directional contributions agree in sign; low when mixed or conflicting
    active_contribs = [c for c in contributions.values() if abs(c) > 0.005]
    if len(active_contribs) >= 2:
        pos_sum = sum(c for c in active_contribs if c > 0)
        neg_sum = sum(abs(c) for c in active_contribs if c < 0)
        dominant_share = max(pos_sum, neg_sum) / max(pos_sum + neg_sum, 1e-6)
        agreement = float(2.0 * (dominant_share - 0.5))  # Scales [0.5, 1.0] -> [0.0, 1.0]
    elif len(active_contribs) == 1:
        agreement = 0.5
    else:
        agreement = 0.0

    # Quality flags penalty
    flag_penalty = 0.20 * len(fv.quality_flags)
    conviction = float(np.clip(agreement * (1.0 - flag_penalty), 0.0, 1.0))

    # 3. Range regime score: high when price is pinned between close walls
    f1 = abs(features.get("F1_call_wall_prox", 0.0))
    f2 = abs(features.get("F2_put_wall_prox", 0.0))
    range_regime_score = float(min(f1, f2))  # High when both walls are near

    # 4. Scenario Logits
    # z_bull = a * D * C
    # z_bear = -a * D * C
    # z_base = b + c * (1 - C) + d * range_regime_score
    p = config.scoring
    z_bull = p.a * directional_score * conviction
    z_bear = -p.a * directional_score * conviction
    z_base = p.b + p.c * (1.0 - conviction) + p.d * range_regime_score

    # Softmax probabilities
    logits = np.array([z_bull, z_base, z_bear], dtype=float)
    # Numerical stability shift
    exp_logits = np.exp(logits - np.max(logits))
    probs = exp_logits / np.sum(exp_logits)

    p_bull = float(probs[0])
    p_base = float(probs[1])
    p_bear = float(probs[2])

    # Determine dominant scenario
    names = ["bull", "base", "bear"]
    scenarios = [
        ScenarioProb(name="bull", prob=p_bull, calibrated=False),
        ScenarioProb(name="base", prob=p_base, calibrated=False),
        ScenarioProb(name="bear", prob=p_bear, calibrated=False),
    ]

    dominant_name = names[int(np.argmax(probs))]
    no_edge = (conviction <= p.min_conviction) or (len(active_contribs) == 0) or (len(fv.quality_flags) >= 2)

    return ScenarioSet(
        ts=fv.ts,
        scenarios=scenarios,
        dominant_scenario=dominant_name,
        conviction=conviction,
        calibrated=False,
        model_version="v1.0-heuristic",
        n_train_samples=0,
        brier_oos=None,
        contributions=contributions,
        no_edge=no_edge,
    )
