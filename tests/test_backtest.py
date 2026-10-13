"""Unit tests for Phase 5: Backtest Replay Engine and ML Calibration."""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta

from target_engine.backtest import BacktestReplayEngine, compute_multiclass_brier_score
from target_engine.calibration import TargetEngineCalibrator


@pytest.fixture
def synthetic_walk_data():
    """Generate 50 chronological 15-minute synthetic snapshots with a trending move."""
    base_time = datetime(2026, 10, 12, 9, 15, 0)
    rows = []
    spot = 25000.0
    for i in range(50):
        t = base_time + timedelta(minutes=15 * i)
        # Upward drift
        spot += float(np.random.normal(10.0, 15.0))
        rows.append({
            "ts": t,
            "spot": spot,
            "fut": spot + 20.0,
            "atm_iv": 0.15,
            "expiry": "2026-10-15",
            "pcr_oi": 1.1 + (i * 0.01),
        })
    return pd.DataFrame(rows)


def test_brier_score_metric():
    preds = [
        {"bull": 0.8, "base": 0.1, "bear": 0.1},
        {"bull": 0.2, "base": 0.7, "bear": 0.1},
    ]
    actual = ["bull", "base"]
    brier = compute_multiclass_brier_score(preds, actual)
    # Perfect would be 0, here near perfect -> should be small (< 0.2)
    assert brier < 0.2


def test_backtest_replay_run(synthetic_walk_data):
    engine = BacktestReplayEngine()
    metrics = engine.run_replay(synthetic_walk_data, horizon_minutes=60)

    assert metrics.total_samples > 0
    assert 0.0 <= metrics.brier_score_model <= 2.0
    assert 0.0 <= metrics.directional_accuracy_pct <= 100.0
    assert len(metrics.summary) > 0


def test_calibration_matrix_and_training(synthetic_walk_data):
    calibrator = TargetEngineCalibrator()
    X, y = calibrator.build_training_matrix(synthetic_walk_data, horizon_minutes=60)

    assert len(X) > 0
    assert len(y) == len(X)

    res = calibrator.train_and_evaluate(X, y)
    assert isinstance(res.promoted, bool)
    assert len(res.message) > 0
