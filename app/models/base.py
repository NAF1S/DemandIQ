"""Shared forecasting interfaces and accuracy metrics."""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.models.features import TARGET, prepare_features


@dataclass
class ForecastResult:
    forecast_date: date
    predicted_quantity: float
    lower_bound: float
    upper_bound: float


@dataclass
class ModelMetrics:
    mae: float
    mape: float
    rmse: float
    coverage_95: float
    bias: float
    wape: float
    pinball_loss: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


class BaseForecaster(ABC):
    """Fit / predict / holdout-backtest contract for every DemandIQ model."""

    name: str = "base"
    version: str = "base"
    min_rows: int = 14

    def __init__(self) -> None:
        self._frame: pd.DataFrame | None = None
        self._fitted = False

    # ------------------------------------------------------------------ lifecycle

    def fit(self, df: pd.DataFrame) -> "BaseForecaster":
        frame = prepare_features(df)
        if len(frame) < 2:
            raise ValueError("At least two daily rows are required to fit a forecaster.")
        self._frame = frame
        self._fit(frame)
        self._fitted = True
        return self

    def predict(self, periods: int = 14) -> list[ForecastResult]:
        if not self._fitted or self._frame is None:
            raise RuntimeError("Call fit() before predict().")
        periods = int(periods)
        if periods < 1:
            raise ValueError("periods must be positive.")
        return self._predict(periods, self._frame)

    def backtest(self, df: pd.DataFrame, horizon: int = 14) -> ModelMetrics:
        """Single holdout backtest: train on all but the last ``horizon`` days."""
        frame = prepare_features(df)
        horizon = int(horizon)
        if horizon < 1 or len(frame) < horizon + 2:
            raise ValueError("Not enough rows for the requested backtest horizon.")

        train = frame.iloc[:-horizon]
        test = frame.iloc[-horizon:]

        model = copy.deepcopy(self)
        model.fit(train)
        predictions = model.predict(horizon)

        actual = test[TARGET].to_numpy(dtype=float)
        predicted = np.array([item.predicted_quantity for item in predictions], dtype=float)
        lower = np.array([item.lower_bound for item in predictions], dtype=float)
        upper = np.array([item.upper_bound for item in predictions], dtype=float)

        return self._metrics(actual, predicted, lower, upper)

    # ------------------------------------------------------------------- contract

    @abstractmethod
    def _fit(self, frame: pd.DataFrame) -> None:
        ...

    @abstractmethod
    def _predict(self, periods: int, frame: pd.DataFrame) -> list[ForecastResult]:
        ...

    def get_hyperparameters(self) -> dict[str, Any]:
        return {}

    def feature_importance(self) -> dict[str, float]:
        return {}

    # -------------------------------------------------------------------- metrics

    @staticmethod
    def _metrics(
        actual: np.ndarray,
        predicted: np.ndarray,
        lower: np.ndarray,
        upper: np.ndarray,
    ) -> ModelMetrics:
        residual = predicted - actual
        mae = float(np.mean(np.abs(residual)))
        rmse = float(np.sqrt(np.mean(residual**2)))
        mask = actual > 0
        mape = float(np.mean(np.abs(residual[mask]) / actual[mask]) * 100) if mask.any() else 0.0
        coverage = float(np.mean((actual >= lower) & (actual <= upper)))
        bias = float(np.mean(residual))

        total_actual = float(np.sum(np.abs(actual)))
        wape = float(np.sum(np.abs(residual)) / total_actual * 100) if total_actual > 0 else 0.0

        def pinball(observed: np.ndarray, forecast: np.ndarray, quantile: float) -> float:
            delta = observed - forecast
            return float(np.mean(np.maximum(quantile * delta, (quantile - 1.0) * delta)))

        pinball_loss = float(
            np.mean(
                [
                    pinball(actual, predicted, 0.5),
                    pinball(actual, lower, 0.025),
                    pinball(actual, upper, 0.975),
                ]
            )
        )

        return ModelMetrics(
            mae=round(mae, 4),
            mape=round(mape, 4),
            rmse=round(rmse, 4),
            coverage_95=round(coverage, 4),
            bias=round(bias, 4),
            wape=round(wape, 4),
            pinball_loss=round(pinball_loss, 4),
        )
