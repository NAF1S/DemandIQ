"""Fit one entity, backtest it, and produce forward predictions.

The module is deliberately database-free so the same code runs in the local
test suite, the EC2 CLI, and the Modal training container.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from app.etl.quality import validate_features
from app.models.base import BaseForecaster, ForecastResult, ModelMetrics
from app.models.features import prepare_features
from app.models.selection import ModelChoice, create_model, select_model


@dataclass
class EntityTraining:
    entity_type: str
    entity_id: int
    model: BaseForecaster
    model_name: str
    version: str
    choice: ModelChoice
    metrics: ModelMetrics
    predictions: list[ForecastResult]
    frame: pd.DataFrame
    artifact_path: str | None = None
    warning: str | None = None


def _fit_and_predict(model_name: str, frame: pd.DataFrame, periods: int) -> tuple[BaseForecaster, ModelMetrics, list[ForecastResult]]:
    model = create_model(model_name)
    metrics = model.backtest(frame, horizon=periods)
    model.fit(frame)
    predictions = model.predict(periods)
    return model, metrics, predictions


def train_entity(
    features: pd.DataFrame,
    entity_type: str,
    entity_id: int,
    periods: int = 14,
    engine: str = "auto",
    artifact_dir: str | Path | None = None,
    run_id: str | None = None,
    organization_id: int = 1,
) -> EntityTraining:
    """Train one entity using the Phase 6 selection rules."""
    if periods < 1:
        raise ValueError("periods must be positive.")

    frame = prepare_features(features)
    if len(frame) < 2:
        raise ValueError(f"{entity_type}:{entity_id} has too few daily rows to train.")

    quality = validate_features(frame, entity_id, entity_type)
    choice = select_model(frame, quality=quality, entity_type=entity_type)
    model_name = choice.model if engine in (None, "", "auto") else engine

    warning: str | None = None
    try:
        model, metrics, predictions = _fit_and_predict(model_name, frame, periods)
    except Exception as exc:  # optional dependency or model-specific failure
        if model_name == "velocity":
            raise
        warning = f"{model_name} failed ({exc}); fell back to velocity."
        model_name = "velocity"
        model, metrics, predictions = _fit_and_predict(model_name, frame, periods)

    artifact_path: str | None = None
    if artifact_dir is not None:
        relative = (
            Path(str(organization_id))
            / entity_type
            / f"{entity_id}-{model.version}-{run_id or 'latest'}.joblib"
        )
        target = Path(artifact_dir) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, target)
        artifact_path = str(target)

    return EntityTraining(
        entity_type=entity_type,
        entity_id=int(entity_id),
        model=model,
        model_name=model_name,
        version=model.version,
        choice=choice,
        metrics=metrics,
        predictions=predictions,
        frame=frame,
        artifact_path=artifact_path,
        warning=warning,
    )


def forecast_rows(result: EntityTraining, organization_id: int) -> list[dict[str, Any]]:
    return [
        {
            "organization_id": organization_id,
            "entity_type": result.entity_type,
            "entity_id": result.entity_id,
            "forecast_date": item.forecast_date,
            "predicted_quantity": item.predicted_quantity,
            "lower_bound": item.lower_bound,
            "upper_bound": item.upper_bound,
            "model_version": result.version,
        }
        for item in result.predictions
    ]
