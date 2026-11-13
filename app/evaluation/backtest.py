"""Walk-forward evaluation for the Phase 6/7 forecasters."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.models.base import BaseForecaster, ModelMetrics
from app.models.features import TARGET, prepare_features
from app.models.selection import create_model


def average_metrics(metrics: list[ModelMetrics]) -> dict[str, float]:
    if not metrics:
        return {}

    keys = ("mae", "mape", "rmse", "coverage_95", "bias", "wape", "pinball_loss")
    return {
        key: round(float(np.mean([getattr(item, key) for item in metrics])), 4)
        for key in keys
    }


def walk_forward_metrics(
    features: pd.DataFrame,
    model_name: str,
    test_periods: int = 14,
    n_splits: int = 3,
    step_size: int = 7,
) -> list[ModelMetrics]:
    """Three expanding-window holdouts, newest window last."""
    frame = prepare_features(features)
    results: list[ModelMetrics] = []

    for split in range(n_splits):
        test_end = len(frame) - split * step_size
        test_start = test_end - test_periods
        train_end = test_start

        if train_end < 14 or test_start < 0 or test_end <= test_start:
            break

        train = frame.iloc[:train_end]
        test = frame.iloc[test_start:test_end]

        model = create_model(model_name)
        model.fit(train)
        predicted_items = model.predict(test_periods)

        actual = test[TARGET].to_numpy(dtype=float)
        predicted = np.array([item.predicted_quantity for item in predicted_items], dtype=float)
        lower = np.array([item.lower_bound for item in predicted_items], dtype=float)
        upper = np.array([item.upper_bound for item in predicted_items], dtype=float)

        if len(predicted) != len(actual):
            raise ValueError("Prediction length does not match the holdout window.")

        results.append(BaseForecaster._metrics(actual, predicted, lower, upper))

    return results


def walk_forward_summary(
    features: pd.DataFrame,
    model_name: str,
    test_periods: int = 14,
    n_splits: int = 3,
    step_size: int = 7,
) -> dict[str, Any]:
    model = create_model(model_name)
    try:
        folds = walk_forward_metrics(
            features,
            model_name,
            test_periods=test_periods,
            n_splits=n_splits,
            step_size=step_size,
        )
    except Exception as exc:
        return {
            "model": model_name,
            "version": model.version,
            "error": str(exc),
            "splits": 0,
        }

    if not folds:
        return {
            "model": model_name,
            "version": model.version,
            "error": "not enough rows for a walk-forward split",
            "splits": 0,
        }

    return {
        "model": model_name,
        "version": model.version,
        "splits": len(folds),
        "metrics": average_metrics(folds),
        "folds": [item.as_dict() for item in folds],
    }
